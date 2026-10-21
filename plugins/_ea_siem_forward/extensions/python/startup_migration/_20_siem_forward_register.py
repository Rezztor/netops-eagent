"""Register the audit sink at startup.

Extensions load only from enabled plugins (`plugins.get_enabled_plugin_paths`),
so a disabled plugin never reaches this file and never registers a sink -- the
forwarding is off because the code that would do it was never wired in, not
because a flag says so.

Enabling the plugin at runtime does not re-run startup, which is why
`sender.ensure_registered()` is idempotent and is called again from the config
API handler when an administrator saves a destination.
"""

from helpers.extension import Extension
from plugins._ea_siem_forward.helpers import sender


class SiemForwardRegister(Extension):
    def execute(self, **kwargs):
        sender.ensure_registered()
