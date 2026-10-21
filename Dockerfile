# Rezztor EAgent runtime image.
#
# The source always comes from the build context; there is no branch-based
# variant that clones from GitHub any more. Build from the repository root:
#   docker build -t rezztor/eagent:latest --build-arg CACHE_DATE=$(date +%s) .
#
# The base image is built from this repository, not pulled from a third party:
#   docker build -t rezztor/eagent-base:latest docker/base/
#
# The base is built on debian:stable-slim. Upstream used
# kalilinux/kali-rolling, whose offensive toolchain a registry scanner will flag
# and which none of this application uses; Debian stable also gives predictable
# package versions where a rolling distribution can shift under a rebuild.
#
# Two stages, because a single-stage build cannot un-ship a layer.
# `COPY ./ /git/eagent` writes the whole source tree into a layer; deleting it
# in a later RUN hides it from the running container but leaves it recoverable
# from the image with `docker save`. Everything a customer receives is built in
# `builder` below and copied forward selectively, so the source tree exists in
# no layer of the final image
# (docs/internal/research/customer-packaging-and-delivery-plan.md §2).


# ---------------------------------------------------------------------------
# Stage 1: build. Discarded entirely -- nothing here reaches the customer
# except what the runtime stage explicitly copies.
# ---------------------------------------------------------------------------
FROM rezztor/eagent-base:latest AS builder

# Retained because the /ins scripts still take it as a positional argument.
ARG BRANCH=local
ENV BRANCH=$BRANCH

# Where the embedding model is cached. install_eagent.sh runs preload.py so the
# first container start does not download it -- but the default cache is the
# builder's ~/.cache, and this is a two-stage build: that stage is discarded and
# the model went with it, leaving every start to fetch it from huggingface.co.
# An appliance in an air-gapped rack got nothing and ran on without embeddings.
#
# Not a per-user path: the application runs as uid 1000, not root.
ENV HF_HOME=/opt/models \
    SENTENCE_TRANSFORMERS_HOME=/opt/models

# Copy filesystem files to root
COPY ./docker/run/fs/ /
# Application source
COPY ./ /git/eagent

# pre installation steps
RUN bash /ins/pre_install.sh $BRANCH

# install application dependencies
RUN bash /ins/install_eagent.sh $BRANCH

# cachebuster: re-run the install so a source change invalidates this layer
ARG CACHE_DATE=none
RUN echo "cache buster $CACHE_DATE" && bash /ins/install_eagent2.sh $BRANCH

# The preload swallows its own errors -- it is a warm cache, not a correctness
# gate -- so a download that failed would otherwise leave an empty directory,
# copy forward cleanly, and be discovered by a customer with no internet. This
# is the one place where an empty cache is still cheap to fix.
RUN test -s "$(find /opt/models -name '*.safetensors' -o -name 'pytorch_model.bin' | head -1)" \
    || { echo "no embedding model under /opt/models: preload.py did not download it" >&2; exit 1; }

# build the web frontend
# Must run after the source is in place and before the source is assembled
# into /eagent below, or the image ships without frontend/dist and quietly
# serves the inherited interface instead.
RUN bash /ins/build_frontend.sh $BRANCH

# post installation steps
RUN bash /ins/post_install.sh $BRANCH

# Stamp the version. .git is excluded from the build context, so without this
# a delivered instance reports no version at all -- to its own health
# endpoint, and to the licence server on every heartbeat.
ARG BUILD_VERSION=unknown
ARG BUILD_COMMIT=
# "open" only for a lab build (ADR 0082), "uat" for a UAT build with its trust JSON
# in BUILD_UAT_TRUST (ADR 0085). Not an ENV: nothing at run time can read it or set it.
ARG BUILD_LICENCE=
ARG BUILD_UAT_TRUST=
ENV BUILD_VERSION=$BUILD_VERSION BUILD_COMMIT=$BUILD_COMMIT
RUN BUILD_LICENCE="$BUILD_LICENCE" BUILD_UAT_TRUST="$BUILD_UAT_TRUST" bash /ins/write_build_info.sh

# Assemble exactly what the runtime stage will take. Doing this here rather
# than with several COPY --from lines keeps "what ships" in one place, next
# to the build that produced it.
#
# node_modules is removed by build_frontend.sh, but a failed or partial build
# can leave it behind; a customer image has no use for it either way.
#
# The build's own inputs stop here too. .dockerignore keeps the engineering
# notes and the tests out of the context; these three the build had to have,
# and the machine has no use for them: the frontend's sources (it serves
# frontend/dist), the files this image's own / was copied from, and the
# dependency lists with their comments (the two executors' images are built
# from theirs, so the context keeps them).
RUN cp -a /git/eagent/. /eagent/ \
    && find /eagent/frontend -mindepth 1 -maxdepth 1 ! -name dist -exec rm -rf {} + \
    && rm -rf /eagent/docker \
    && find /eagent -name 'requirements*.txt' -delete \
    && find /eagent -name '__pycache__' -type d -prune -exec rm -rf {} + \
    && find /eagent -name '*.pyc' -delete


# ---------------------------------------------------------------------------
# Stage 2: runtime. This is the delivered image.
# ---------------------------------------------------------------------------
FROM rezztor/eagent-base:latest

ARG BRANCH=local
ENV BRANCH=$BRANCH

# Startup scripts. Small, and needed before anything else runs.
COPY ./docker/run/fs/ /

# Clear the base image's own virtualenv before taking the built one.
#
# COPY merges directories, it does not replace them. The base ships a venv
# with numpy 2.5.2; the build installed 1.26.4 over it (2026-09). Merging left both
# dist-info directories and a mix of the two versions' compiled extensions,
# so numpy imported on its own but died as soon as anything real
# (sentence-transformers) loaded it: "numpy._core.multiarray failed to
# import". The single-stage build never hit this because it installed into
# that venv in place.
RUN rm -rf /opt/venv-eagent

# The Python environment built in stage 1. Build *output*, not build *input*:
# it contains no application source beyond what the assemble step copied.
COPY --from=builder /opt/venv-eagent /opt/venv-eagent
# Owned by the application's account from the copy on, for the reason given at
# the model below: the application tree is large, and the `chown -R`
# further down put every file of it into the image a second time.
COPY --from=builder --chown=1000:1000 /eagent /eagent

# The embedding model, and the two variables that make the application look for
# it here rather than in a cache directory that does not exist.
#
# --chown by number, because the eagent account is created further down and a
# `chown -R` there would rewrite every file into a second 88 MB layer. Reading
# the baked model does not need the ownership; writing a different one, on a
# deployment that has internet and chooses one, does.
COPY --from=builder --chown=1000:1000 /opt/models /opt/models
ENV HF_HOME=/opt/models \
    SENTENCE_TRANSFORMERS_HOME=/opt/models

# System packages the runtime needs on top of the base image.
RUN bash /ins/pre_install.sh $BRANCH \
    && bash /ins/post_install.sh $BRANCH

# The account the application runs as.
#
# The application does not need root and does not have it. There is no USER
# line on purpose: the container starts as root only so that initialize.sh can
# hand this account the usr volume and the update inbox -- root's on every
# installation made before 2026-10-16 -- and then it drops to uid 1000 with
# setpriv before anything serves. The compose files leave that root four
# capabilities and no-new-privileges (ADR 0022, amended 2026-10-16).
RUN useradd --create-home --uid 1000 --shell /bin/bash eagent \
    && mkdir -p /eagent/usr \
    && find /eagent ! -user eagent -exec chown eagent:eagent {} + \
    && chown eagent:eagent /opt/venv-eagent

# Expose ports
# Port 22 is gone: code execution moved to docker/runner, so this container has
# no reason to accept shell connections. 8080, not 80: the application is not
# root and holds no capability to bind below 1024 (run_eagent.sh).
EXPOSE 8080 9000-9009

RUN chmod +x /exe/initialize.sh /exe/run_eagent.sh

# fix ownership as root, then serve as eagent
CMD ["/exe/initialize.sh", "$BRANCH"]
