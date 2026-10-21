"""The image, read as text (it cannot be built here): pinned and hashed downloads, a mirror with no `direct`, and every
file the Dockerfile copies actually exists. The coordinator builds it and reads back `tofu version` and the mirror."""
import glob
import re
from pathlib import Path

from cloud_executor import runner

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "cloud_executor" / "Dockerfile").read_text(encoding="utf-8")
FETCH, FINAL = DOCKERFILE.split("\nFROM debian:stable-slim\n")


def test_the_versions_in_the_image_are_the_ones_the_runner_writes_into_the_provider_blocks():
    for name, constant in (("AZURERM_VERSION", runner.AZURERM_VERSION), ("AZAPI_VERSION", runner.AZAPI_VERSION)):
        assert re.findall(rf"{name}=([0-9.]+)", DOCKERFILE) == [constant, constant]  # the fetch stage and the final stage


def test_every_download_is_checked_against_a_sha256_written_in_the_file():
    hashes = re.findall(r"=([0-9a-f]{64})[; ]", FETCH)
    assert len(hashes) == 6 and len(set(hashes)) == 6  # OpenTofu, azurerm, azapi for amd64 and arm64
    assert FETCH.count("sha256sum -c -") == 1 and FETCH.count('fetch "') == 3
    assert FETCH.index("unzip -o /tmp/azurerm.zip") > FETCH.index('"$azurerm" /tmp/azurerm.zip'), "unpack only after the check"
    assert "unzip" in FETCH and "curl" not in FINAL, "the downloader belongs in the throwaway stage only"


def test_the_mirror_has_no_direct_block_and_names_only_the_two_providers():
    config = (ROOT / "cloud_executor" / "cli.tfrc").read_text(encoding="utf-8")
    code = "\n".join(line for line in config.splitlines() if not line.lstrip().startswith("#"))
    assert "filesystem_mirror" in code and "direct" not in code and "network_mirror" not in code
    assert code.count("registry.opentofu.org/") == 2
    assert "COPY cloud_executor/cli.tfrc /etc/tofu/cli.tfrc" in FINAL
    assert "a provider outside the mirror was installed" in FINAL, "the build must prove an outside provider fails"


def test_it_runs_unprivileged_with_one_process_and_no_tcp_port():
    assert "USER ${EXECUTOR_USER}" in FINAL and "EXECUTOR_UID=1000" in FINAL
    assert "EXPOSE" not in DOCKERFILE
    assert FINAL.rstrip().endswith('CMD ["python", "-c", "from cloud_executor.app import main; main()"]')
    assert FINAL.index("USER ${EXECUTOR_USER}") > FINAL.index("useradd")


def test_every_file_the_image_copies_exists():
    for line in re.findall(r"(?m)^COPY (?!--from)(.+)$", DOCKERFILE):
        *sources, _destination = line.split()
        for source in sources:
            if source == "plugins/_ea_azure/helpers/prices.py" or source.startswith("cloud_executor/prices"):
                assert (ROOT / source).exists()
                continue
            assert glob.glob(str(ROOT / source)), f"the Dockerfile copies {source}, which does not exist"


def test_the_image_installs_nothing_the_project_does_not_already_pin():
    """Every package is one the application or the Ansible executor already pins (waitress is the latter's)."""
    known = {m.lower().replace("_", "-") for f in ("requirements.txt", "executor/requirements.txt")
             for m in re.findall(r"(?m)^([A-Za-z0-9_.-]+)", (ROOT / f).read_text())}
    ours = re.findall(r"(?m)^([A-Za-z0-9_.-]+)", (ROOT / "cloud_executor" / "requirements.txt").read_text())
    assert ours and not [n for n in ours if n.lower().replace("_", "-") not in known], "a new dependency"


def test_the_notice_names_each_upstream_and_its_licence():
    notice = (ROOT / "cloud_executor" / "NOTICE").read_text(encoding="utf-8")
    for needle in ("opentofu/opentofu", "terraform-provider-azurerm", "terraform-provider-azapi", "MPL-2.0"):
        assert needle in notice
