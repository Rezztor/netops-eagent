# Scripts DOX

## Purpose

- Own maintainer and automation scripts that live outside GitHub workflow directories.
- Keep scripts deterministic, documented by nearby callers, and safe to run in clean checkouts.

## Ownership

- `openrouter_release_notes_system_prompt.md` is consumed by `.github/scripts/docker_release_plan.py`.
- Licences are issued and signed in the licence-server repository, not here (ADR 0078); this directory holds no licence tool.
- `sync_azure_skills.sh` (and `sync_azure_skills.py`) vendors Microsoft's Azure skills as Markdown into `plugins/_ea_azure/skills/azure-microsoft-guidance/reference/` (ADR 0076); developer-time only (a security review by default that writes `tmp/azure-skills-review-<commit>.md`; `--accept --reviewer NAME` imports only a reviewed commit), never run by the product, covered by `plugins/_ea_azure/tests/test_azure_microsoft_guidance.py`.
- `sign_release.py` makes the release keys and the update packages for installed appliances (ADR 0080): `keypair` writes an encrypted private key outside the repository and the public half for `deploy/iso/keys/`; `package` compares the application image of the new version with the base release's and writes one `.rezupd` file. Packages are a strict chain: `package` needs `--min-from` (the package before it) unless `--first` (the first package of a core, min_from = the core); a bridge (`--base` = `--version`, every image whole) still names `--min-from`, the last package of the old core, and is compared with that image (`min_from_of`). It decides, from the changed paths alone, which image travels whole, which host files go along (the four, the guard and seven units, `HOST`), whether the database changes and whether a restart is needed (`classify`), and `deploy/iso/host/rezztor-update` reads every field it writes: change the two together. With `--os-snapshot` it adds Debian's updates since the core (ADR 0092): the tools container (`deploy/iso/tools.Dockerfile`) resolves `apt-get upgrade --with-new-pkgs` for the core's lists (`deploy/iso/cores/`) against `snapshot.debian.org` at that time and Docker's pinned versions (`os_part`); `os-updates` says what that would be and signs nothing. A package with Debian's updates, the guard or a unit is format 4, any other format 3; `--format 3` makes one for a tool from before ADR 0092. Tests mock the container; nothing in them touches the network. A lab image is never packaged; a UAT image only as a version that ends in `-uat`, and a `-uat` version only from a UAT image (`refuse_stamp`, ADR 0085). Never shipped in an image (`.dockerignore`); covered by `tests/test_update_tool.py`.
- `build_release.sh TAG` builds what a customer receives (ADR 0084): a fresh clone of a pushed tag, the five images
  under the version as their tag, and a RELEASE ISO from that clone. `build_release.sh core TAG --bridge-from V ...`
  adds the bridge package from the same images; `build_release.sh package TAG --base CORE (--min-from V | --first) ...`
  builds the images and signs a package, no ISO. Both sign with the clone's `sign_release.py`; `--os-snapshot`,
  `--os-core` and `--format` are passed to it. `build_local.sh` and `build_iso.sh` are the
  development builds it calls; `build_iso.sh` refuses the RELEASE kind outside a clean checkout of the tag.
  `tests/test_iso_build.py` runs the refusals. A lab build (`BUILD_LICENCE=open`, ADR 0082) and a UAT build
  (`BUILD_LICENCE=uat` with `REZZTOR_UAT_TRUST`, ADR 0085) are tagged `lab` and `uat`, never `latest`, and
  `build_release.sh` unsets every variable of both.
- `build_update_bootstrap.sh` makes the one-time file that puts the update tool on a machine installed before the tool existed.
- Additional repository maintenance scripts belong here when they are not runtime application code.

## Local Contracts

- Do not commit secrets, generated credentials, private release notes, or local machine paths.
- Scripts used by CI must have stable inputs and fail with actionable errors.
- Keep script behavior synchronized with `.github/AGENTS.md`, workflow YAML, and tests.

## Work Guidance

- Prefer standard library Python or simple shell-compatible assets unless a dependency already exists for the script's runtime.
- Keep prompts and automation inputs concise and version-controlled.
- Update callers when renaming or moving scripts.

## Verification

- Run targeted tests for any automation script with coverage.
- For release-note prompt changes, inspect generated output format expectations in `.github/scripts/docker_release_plan.py`.

## Child DOX Index

No child DOX files.
