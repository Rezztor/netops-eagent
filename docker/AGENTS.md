# Docker DOX

## Purpose

- Own Docker build contexts and runtime container definitions.
- Keep framework runtime, agent execution runtime, exposed ports, mounted paths, and image build assumptions explicit.

## Ownership

- `base/` owns the base image context.
- `run/` owns the runnable image context and compose file.
- `runner/` owns the code-execution runner image: its sshd (root, with the five
  capabilities the compose file lists; every session is an unprivileged slot
  account), the interpreters agent code runs on, and the document toolchain.
- The root `Dockerfile` is owned by the root contract but must stay compatible with this directory.

## Local Contracts

- Preserve the two-runtime model: the Python 3.12 framework runtime under `/opt/venv-eagent` runs the WebUI, APIs, scheduler, framework imports, and plugin hooks; the Python 3.13 agent execution runtime under `/opt/venv` runs agent terminal tasks and user code.
- Verify backend imports and plugin hooks with `/opt/venv-eagent`; packages installed into `/opt/venv` do not prove framework compatibility.
- Do not bake secrets, local `.env` values, or user data into images.
- The root `Dockerfile` copies the whole build context (`COPY ./`), so `.dockerignore` at the repository root is the list of what ships. A pattern there matches the root of the context only unless it begins with `**/`: `*.env` did not cover `docker/run/.env`. Gitignored files are still in the context. `tests/test_delivery_hygiene.py` holds the entries that must stay.
- **A customer gets what the machine runs and nothing that explains, tests or builds it** (owner's rule, 2026-10-12). `.dockerignore` keeps out every `*.dox.md`, `AGENTS.md` and `README.md`, `CLAUDE.md`, `docs/`, every `tests/` folder, `deploy/`, `scripts/` and the Dockerfiles; the assemble step of the root `Dockerfile` removes what the build itself needed (`docker/`, the frontend's sources, the `requirements*.txt` files). `scripts/build_offline_bundle.sh` looks inside the three images and stops when any of it is back. A new file the product reads at run time must not carry one of those names. Six skills for developing the framework and its plugins, and `knowledge/main/about/architecture.md`, are held back the same way (owner's decision, same day): a delivered skill, prompt or knowledge note must not point at one of them.
- **No source of ours in a delivered image** (stage 3 of the same rule, 2026-10-12). `compile_python.py` in this folder compiles every `.py` under the image's code root into a native module in its place with Nuitka and deletes the source; docstrings are left out, and link-time optimisation is on (a module is 320 KB with it and 880 KB without, 646 of them). The app image runs it in the builder stage after the assemble step (Nuitka in its own `/opt/venv-compile`, made from the interpreter the application runs on, never copied forward); the two executor images have a `compile` stage on the same Debian as their final stage. A compiled module serves one Python version, so the compile must run on the interpreter the image runs. The tool's `stays` is the whole list of what remains source and why: `__init__.py` (Nuitka compiles a package, not its file alone; keep logic out of them), `prepare.py`, anything under a `playbooks/` folder (Ansible reads source), a command's script under `commands/` (shown as text in the Commands manager). `run_ui.py` is compiled and replaced by a two-line launcher. The bundle script lets exactly those kinds through and stops on any other `.py`; `tests/test_delivery_hygiene.py` keeps the two lists together.
- **What compiled code changes for whoever writes the product.** A module started with `python -m` or by path needs a `main()` and is started with `python -c "from x import main; main()"`: `-m` and a path run source only. A compiled file has one set of globals per process however often it is loaded, so `helpers.modules.import_module` loads each once and shares it with `import` (read its DOX before loading code by path). A test may read source text; the product may not (`inspect.getsource`, `Path(__file__).read_text()` on a `.py` of ours fail in the image). Nuitka resolves `from <lazy package> import name` to the submodule that defines it, which only a test that patches the package attribute notices.
- **The image still tells the update builder what it was built from.** It carries no Dockerfile, no `docker/` and no requirements file, and those are what says that an image must travel whole in an update. `scripts/build_local.sh app` writes their hashes to `build_inputs.sha256` (git-ignored, in the build context, shipped as `/eagent/build_inputs.sha256`), and `scripts/sign_release.py` reads the list back and refuses an image without one. Four files of `deploy/` are let into the image by exceptions in `.dockerignore` (the compose file and the three host tools): they are on every appliance's own disk, and an update package takes the new ones from the image. Build the app image with `scripts/build_local.sh`, not with a bare `docker build`.
- The embedding model is the exception that must be baked: `/opt/models`, with
  `HF_HOME` and `SENTENCE_TRANSFORMERS_HOME` pointing at it in both stages and
  a build-time check that it landed. It is a two-stage build, so anything the
  builder writes outside `/eagent` and the virtualenv has to be copied forward
  deliberately or an air-gapped install reaches for huggingface.co on every
  start.
- Keep compose mounts aligned with `usr/` and other runtime-state expectations.
- The runner's slot accounts (`eagent-NNN`) have `runner/slot-shell` as their login shell, never bash directly. sshd runs a command as `<shell> -c <command>` and Debian's bash reads `~/.bashrc` first there; the application empties a slot by sending commands as that slot, so the account being emptied must not get to run first. A session with no command is still a bash login shell. `tests/test_deployment_hardening.py` holds this.
- In slot mode the shared `eagent` account does not log in: the entrypoint starts sshd with `-o DenyUsers=eagent` (a second `AllowUsers` would add to the config's list, not replace it). It is the group that may write every slot's key directory. Single mode keeps it.
- Every slot session starts with limits of its own, set by `runner/slot-shell` with `prlimit` (no PAM): 256 processes and threads per uid (a sixteenth of the runner's compose `pids_limit` of 4096, so no two accounts fill it), 1.5 GB of address space and 2 GiB per file per process. The build reads them back. The size of the workspace volume is the operator's (`deploy/customer/INSTALL.md`, step 5).
- Everything agent code needs goes into the runner image at build time. Every
  writable path there is tmpfs, so a runtime install is lost on restart and
  fetched again -- and a runner that must reach the internet to do its job is one
  whose egress rules can never be tightened.
- Third-party binaries are pinned by version and verified by digest. `curl | sh`
  puts an unexamined HTTP response into the image that executes agent code.
- A toolchain added to the runner is proved in the build, not assumed. A missing
  font renders as boxes rather than an error, so the check must read the result
  back rather than trust an exit status.
- A capability the agent is not told about is one it will reinvent: a tool added
  to the runner needs a line in the prompt that offers it.

## Work Guidance

- Keep Dockerfile steps cache-friendly and explicit about which runtime they target.
- Avoid broad copies of ignored runtime folders.
- Update setup docs when ports, volumes, startup commands, or runtime layout change.

## Verification

- Build the affected Docker context when Docker behavior changes.
- Run Docker-related tests or startup smoke checks when changing runtime entrypoints.

## Child DOX Index

Direct child DOX files:

| Child | Scope |
| --- | --- |
| [base/AGENTS.md](base/AGENTS.md) | Base image Dockerfile, copied filesystem, and installation scripts. |
| [run/AGENTS.md](run/AGENTS.md) | Runnable image Dockerfile, compose example, entrypoints, and install scripts. |

The runner image (`runner/Dockerfile`, `runner/entrypoint.sh`) has no child DOX;
its contracts are the ones above and the reasoning is in the Dockerfile itself.
