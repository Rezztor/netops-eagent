"""Turning the plugin on when the deployment already shipped an executor.

The setup guide calls these two switches, deliberately: running the executor
container is a deployment decision, and enabling the plugin is a runtime one.
That separation earns its keep in one direction -- a plugin enabled with no
container gives clear errors instead of a silent no-op -- but not in the other.
An operator who deployed the executor has already decided they want network
automation, and making them say so a second time in a settings form is a manual
step with no decision in it.

So the deployment's answer becomes the *default*, once. The admin's own choice
is never overwritten: a toggle file under `usr/plugins/` means somebody chose,
and this leaves it exactly as it is.
"""

from __future__ import annotations

import os

from helpers import files, plugins
from helpers.print_style import PrintStyle

from plugins._ea_ansible.helpers import config as plugin_config


def _admin_has_chosen() -> bool:
    """Whether a toggle has been written to the volume for this plugin.

    Only the user directory counts. The plugin ships `.toggle-0` in the image,
    which is the default this function exists to reconsider -- reading it as a
    decision would mean never doing anything.
    """
    root = files.get_abs_path(files.USER_DIR, files.PLUGINS_DIR, plugin_config.PLUGIN_NAME)
    return any(
        files.exists(files.get_abs_path(root, name))
        for name in (plugins.ENABLED_FILE_NAME, plugins.DISABLED_FILE_NAME)
    )


def enable_if_the_deployment_ships_an_executor() -> bool:
    """Returns whether this call enabled the plugin."""
    if not os.getenv(plugin_config.ENV_EXECUTOR_URL):
        return False
    if not plugin_config.executor_token():
        # An address with no token cannot authenticate. Enabling here would
        # produce a plugin that is on and cannot work, which is worse than off.
        return False
    if _admin_has_chosen():
        return False

    plugins.toggle_plugin(
        plugin_config.PLUGIN_NAME, True, project_name="", agent_profile=""
    )
    PrintStyle().print(
        "Network automation: enabled, because this deployment ships an Ansible executor."
    )
    return True
