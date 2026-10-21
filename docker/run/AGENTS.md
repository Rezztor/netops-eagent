# Docker Runtime Image DOX

## Purpose

- Own the runnable Rezztor EAgent image context and local compose example.
- Install Rezztor EAgent from a selected branch onto the base image and prepare runtime entrypoints.

## Ownership

- `Dockerfile` owns branch-based image assembly, exposed ports, and container startup command.
- `docker-compose.yml` owns the local compose service example.
- `build.txt` owns maintainer build and push command notes.
- `fs/exe/` owns runtime entrypoint, self-update, Node eval, and service scripts.
- `fs/ins/` owns preinstall, installation, virtualenv, SSH, and postinstall scripts.
- Files under `fs/` are copied to container root during the runtime build.

## Local Contracts

- `BRANCH` is required for branch-based Docker builds.
- Preserve exposed ports for SSH, HTTP, and tunneled services unless docs and workflows are updated together.
- Keep the two-runtime Python model aligned with the root contract.
- Do not bake secrets, local `.env` values, or user data into the image.
- The image stamp is written by `fs/ins/write_build_info.sh`. `BUILD_LICENCE=enterprise` writes `"licence": "enterprise"` (a release). `BUILD_LICENCE=uat` with a `-uat` version and `BUILD_UAT_TRUST` writes the UAT stamp and `licence_trust.json`. Unset is a Community image: no licence line. `open` is refused.
- Runtime startup must ensure `/eagent/usr/uploads` exists before the application starts.
- Runtime startup raises the soft open-file limit toward `REZZTOR_NOFILE_LIMIT` (default `65535`) before the application starts, bounded by the container hard limit.
- **The application never serves as root** (ADR 0022, amended 2026-10-16). Started as root, `fs/exe/initialize.sh` only chowns the `usr` volume and the update inbox to uid 1000 and re-executes itself through `setpriv`; supervisord is not started. It listens on 8080 (`WEB_UI_PORT`). Anything added to that root branch must work with the four capabilities the compose files grant (`CHOWN`, `DAC_READ_SEARCH`, `SETUID`, `SETGID`) and no `DAC_OVERRIDE`.
- Self-update user-data backups skip Time Travel shadow history under `usr/.time_travel/`.
- Self-update waits up to 180 seconds for the updated or restored WebUI health check by default; `REZZTOR_SELF_UPDATE_HEALTH_TIMEOUT_SECONDS` may override it.
- Successful or already-current self-updates refresh an installed Codex CLI with npm on a best-effort basis; missing CLIs and registry failures must not block Rezztor EAgent startup.

## Work Guidance

- Keep startup scripts explicit about framework runtime versus execution runtime.
- Coordinate tag, branch, and publishing changes with GitHub workflow automation.

## Verification

- Build `docker/run` when changing Dockerfile or install scripts.
- Smoke-test container startup after entrypoint, supervisor, port, or compose changes.

## Child DOX Index

No child DOX files.
