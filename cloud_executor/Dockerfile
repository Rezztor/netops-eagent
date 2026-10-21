# The cloud executor: OpenTofu, two providers, and the API that runs them (ADR 0061).
#
# The application never runs model-written code, and neither does this image run anything the model wrote *as a
# program*: it runs reviewed OpenTofu (a template, or free code that passed the static gate) through two providers,
# with the plan judged by `gate.check_plan` before anything is applied. It is its own container because it accepts
# code, holds Azure keys the application must not hold, and has the one network path to Azure's write APIs.
#
# Nothing downloads at run time. The OpenTofu binary and the two providers are fetched here, once, in a throwaway
# stage, and each is checked against a SHA-256 written in this file before it is used. The hashes were read on
# 3 October 2026 from the registry's download answer and, independently, from each project's own SHA256SUMS release
# file (see NOTICE). Changing a version means changing its hashes in the same commit; an unchecked file fails the build.
#
# Build from the repository root:
#   docker build -f cloud_executor/Dockerfile -t rezztor/eagent-cloud:latest \
#     --build-arg TEMPLATES_COMMIT="$(git rev-parse HEAD)" .
FROM debian:stable-slim AS fetch

ARG TARGETARCH=amd64
ENV DEBIAN_FRONTEND=noninteractive \
    TOFU_VERSION=1.13.1 \
    AZURERM_VERSION=5.8.0 \
    AZAPI_VERSION=2.13.0

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "${TARGETARCH}" in \
      amd64) tofu=8ccbc6f8ee21d2827715f3c6e08a9b3e0209b1e62057c05067ef117e047c1a80; \
             azurerm=a7e3e398a78b382d3e6168357cce2d6b6c55a94ab945e8456e01ce88a326eb30; \
             azapi=bbe4a39522cff59977e1bf623e7d95267d819ef3cf9ba0f597eb066ce1b11b70 ;; \
      arm64) tofu=b9614df40575cc3fc10a8a25025b7245d961da279f715ea3efff4ddae8e6938a; \
             azurerm=dc82ae14bf0468d719bcdc3af4a1ac5e2899a9e98b5bbac9f44bf033c67e5f2d; \
             azapi=e2948388d78d392b7992cc0e76d4f266545ffc8984ba458d7fa6884a5f04f376 ;; \
      *) echo "no pinned hashes for ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    fetch() { curl -fsSL --retry 3 -o "$3" "$1"; echo "$2  $3" | sha256sum -c -; }; \
    mirror=/mirror/registry.opentofu.org; \
    azurerm_dir="$mirror/hashicorp/azurerm/${AZURERM_VERSION}/linux_${TARGETARCH}"; \
    azapi_dir="$mirror/azure/azapi/${AZAPI_VERSION}/linux_${TARGETARCH}"; \
    mkdir -p "$azurerm_dir" "$azapi_dir" /out; \
    fetch "https://github.com/opentofu/opentofu/releases/download/v${TOFU_VERSION}/tofu_${TOFU_VERSION}_linux_${TARGETARCH}.zip" \
          "$tofu" /tmp/tofu.zip; \
    unzip -o /tmp/tofu.zip tofu -d /out; \
    fetch "https://github.com/opentofu/terraform-provider-azurerm/releases/download/v${AZURERM_VERSION}/terraform-provider-azurerm_${AZURERM_VERSION}_linux_${TARGETARCH}.zip" \
          "$azurerm" /tmp/azurerm.zip; \
    fetch "https://github.com/Azure/terraform-provider-azapi/releases/download/v${AZAPI_VERSION}/terraform-provider-azapi_${AZAPI_VERSION}_linux_${TARGETARCH}.zip" \
          "$azapi" /tmp/azapi.zip; \
    unzip -o /tmp/azurerm.zip -d "$azurerm_dir"; \
    unzip -o /tmp/azapi.zip -d "$azapi_dir"; \
    rm -f "$azurerm_dir"/CHANGELOG*.md; \
    chmod 0755 /out/tofu "$azurerm_dir"/terraform-provider-azurerm* "$azapi_dir"/terraform-provider-azapi*; \
    chmod -R a+rX /mirror

FROM debian:stable-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    EXECUTOR_USER=cloud \
    EXECUTOR_UID=1000 \
    VENV=/opt/cloud-venv \
    PYTHONPATH=/app \
    AZURERM_VERSION=5.8.0 \
    AZAPI_VERSION=2.13.0 \
    REZZTOR_CLOUD_STATE_DIR=/var/lib/eagent-cloud \
    REZZTOR_CLOUD_SOCKET=/run/cloud-socket/cloud.sock \
    REZZTOR_CLOUD_RUN_DIR=/tmp/cloud-runs

# The catalogue and the image's version are pinned at build, and /healthz says both out loud. An image built without a
# commit says `unpinned` rather than hiding it.
ARG TEMPLATES_COMMIT=""
ARG VERSION="dev"
ENV REZZTOR_CLOUD_TEMPLATES_COMMIT=${TEMPLATES_COMMIT} \
    REZZTOR_CLOUD_ACTIONS_COMMIT=${TEMPLATES_COMMIT} \
    REZZTOR_CLOUD_VERSION=${VERSION}

RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY cloud_executor/requirements.txt /tmp/requirements.txt
RUN python3 -m venv ${VENV} \
    && ${VENV}/bin/pip install --no-cache-dir --upgrade pip \
    && ${VENV}/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt
ENV PATH="${VENV}/bin:${PATH}"

# OpenTofu, the providers' mirror (unpacked, so `init` links the binaries instead of copying hundreds of megabytes per run), and the CLI configuration that reads only that mirror. Image-owned, root-owned,
# read-only at run time. The licences and sources are in NOTICE.
COPY --from=fetch /out/tofu /usr/local/bin/tofu
COPY --from=fetch /mirror /opt/mirror
COPY cloud_executor/cli.tfrc /etc/tofu/cli.tfrc
COPY cloud_executor/NOTICE /usr/share/doc/eagent-cloud/NOTICE

WORKDIR /app
# The executor package, its reviewed catalogue and the role it expects its identity to hold.
COPY cloud_executor/*.py /app/cloud_executor/
COPY executor/secret_env.py executor/credentials.py executor/store.py /app/executor/
# The read plugin's price list client, copied as it is with its one import pointed at this container's own connection.
COPY plugins/_ea_azure/helpers/prices.py /tmp/prices.py
RUN sed 's/^from helpers import db$/from cloud_executor import db/' /tmp/prices.py > /app/cloud_executor/prices.py \
    && grep -q '^from cloud_executor import db$' /app/cloud_executor/prices.py \
    && ! grep -q 'from helpers' /app/cloud_executor/prices.py \
    && rm /tmp/prices.py
COPY cloud_executor/role.json cloud_executor/templates.yaml cloud_executor/actions.yaml /app/cloud_executor/
COPY cloud_executor/templates/ /app/cloud_executor/templates/
# Reused from the Ansible executor by copying, not duplicated: secrets from env or a file, and the AES-GCM store.
RUN useradd --uid ${EXECUTOR_UID} --create-home --shell /usr/sbin/nologin ${EXECUTOR_USER} \
    && mkdir -p /var/lib/eagent-cloud /run/cloud-socket \
    && chown ${EXECUTOR_UID}:${EXECUTOR_UID} /var/lib/eagent-cloud /run/cloud-socket \
    && chmod 0750 /run/cloud-socket

# Read the results back instead of trusting an exit status. The mirror must hold both providers and init must install
# them from it; and a provider that is not in the mirror must fail, which is the proof that the CLI configuration has
# no `direct` block and the executor cannot fetch code at run time.
RUN set -eux; \
    export TF_CLI_CONFIG_FILE=/etc/tofu/cli.tfrc TF_IN_AUTOMATION=1 TF_DATA_DIR=/tmp/check/.terraform; \
    tofu version; \
    find /opt/mirror -type f -name 'terraform-provider-*' | sort; \
    mkdir -p /tmp/check && cd /tmp/check; \
    printf 'terraform {\n  required_providers {\n    azurerm = { source = "hashicorp/azurerm", version = "= %s" }\n    azapi = { source = "azure/azapi", version = "= %s" }\n  }\n}\n' \
        "${AZURERM_VERSION}" "${AZAPI_VERSION}" > main.tf; \
    tofu init -input=false -no-color; \
    printf 'terraform {\n  required_providers {\n    random = { source = "hashicorp/random" }\n  }\n}\n' > main.tf; \
    rm -rf .terraform .terraform.lock.hcl; \
    if tofu init -input=false -no-color; then echo "a provider outside the mirror was installed" >&2; exit 1; fi; \
    cd / && rm -rf /tmp/check; \
    python -c "import cloud_executor.app, cloud_executor.templates as t; print('api ok,', len(t.load()), 'templates')"; \
    python -c "import cloud_executor.prices; print('prices ok')"

USER ${EXECUTOR_USER}

# The job history, the cost ledger, the guard rails and the sealed keys live here, on the one volume this container
# writes to (besides tmpfs). Created and chowned in the image: a volume arrives root-owned and the executor has dropped
# every capability by the time it would need to fix that.
VOLUME ["/var/lib/eagent-cloud"]

HEALTHCHECK --interval=15s --timeout=8s --retries=20 --start-period=30s CMD ["python", "-c", "from cloud_executor.healthcheck import main; main()"]

# No shell form: the container runs one process and stops when it stops. It listens on a Unix socket and on no TCP port.
CMD ["python", "-c", "from cloud_executor.app import main; main()"]
