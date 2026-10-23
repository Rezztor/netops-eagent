"""scripts/build_local.sh rewrites a UAT build's trust file into the image, field by field.

The UAT update service's address (`update_server`) was added to the trust file and read by the application,
but this step kept only three fields: every UAT image then asked a placeholder address, and Check now said the
update service could not be reached. Found on the UAT appliance after installing a package.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY = "A" * 43  # 32 bytes, base64url without padding
CA = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"


def _uat_trust(tmp_path, trust: dict) -> subprocess.CompletedProcess:
    path = tmp_path / "trust.json"
    path.write_text(json.dumps(trust))
    function = subprocess.run(["sed", "-n", "/^uat_trust() {/,/^}/p", str(ROOT / "scripts" / "build_local.sh")],
                              capture_output=True, text=True, check=True).stdout
    assert function, "uat_trust() is no longer in build_local.sh"
    return subprocess.run(["bash", "-c", function + "\nuat_trust"], capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin", "BUILD_LICENCE": "uat", "REZZTOR_UAT_TRUST": str(path)})


def test_the_update_server_reaches_the_image(tmp_path):
    trust = {"root_keys": {"k1": KEY}, "server": "https://licence.example", "ca_pem": CA,
             "update_server": "https://update.example"}
    out = _uat_trust(tmp_path, trust)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == trust


def test_a_trust_file_without_one_still_builds(tmp_path):
    trust = {"root_keys": {"k1": KEY}, "server": "https://licence.example", "ca_pem": CA}
    out = _uat_trust(tmp_path, trust)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == trust


def test_a_plain_http_update_server_is_refused(tmp_path):
    trust = {"root_keys": {"k1": KEY}, "server": "https://licence.example", "ca_pem": CA,
             "update_server": "http://update.example"}
    assert _uat_trust(tmp_path, trust).returncode != 0
