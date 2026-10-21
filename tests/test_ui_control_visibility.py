import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ui_control_visibility_settings_are_normalized() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import json
from helpers import settings

defaults = settings.get_default_settings()["ui_control_visibility"]
normalized = settings.normalize_settings({
    **settings.get_default_settings(),
    "ui_control_visibility": {
        "time": {"mobile": True, "desktop": False},
        "projectSelector": "invalid",
        "unknown": {"mobile": False},
    },
})["ui_control_visibility"]
print(json.dumps({"defaults": defaults, "normalized": normalized}))
""",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)
    defaults = data["defaults"]
    normalized = data["normalized"]

    assert defaults["time"] == {"mobile": False, "desktop": True}
    assert normalized["time"] == {"mobile": True, "desktop": False}
    assert normalized["projectSelector"] == {"mobile": True, "desktop": True}
    assert "unknown" not in normalized
