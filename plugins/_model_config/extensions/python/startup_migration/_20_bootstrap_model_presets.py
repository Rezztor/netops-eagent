import os

from helpers import files
from helpers.extension import Extension
from helpers.print_style import PrintStyle
from plugins._model_config.helpers import model_config


class BootstrapModelPresets(Extension):
    """Initialize the user preset collection from the presets bundled with this
    repository.

    Upstream downloaded these at first start from
    a public upstream repository. Rezztor EAgent ships its
    presets in-tree instead: startup performs no network I/O, the presets are
    versioned with the code that consumes them, and a deployment cannot pick up
    a change made in someone else's repository.
    """

    def execute(self, **kwargs):
        presets_path = model_config._get_presets_path()

        # Normal startup path once presets exist.
        if os.path.exists(presets_path):
            return "existing"

        try:
            presets = model_config.parse_preset_collection(
                files.read_file(model_config._get_fallback_presets_path())
            )
        except Exception as exc:
            PrintStyle.error(f"Bundled model presets are invalid: {exc}")
            return "error"

        try:
            model_config.save_presets(presets)
        except Exception as exc:
            # Runtime preset resolution still falls back to the local file if
            # initialization cannot be persisted.
            PrintStyle.error(f"Could not persist initial model presets: {exc}")
            return "error"

        return "bundled"
