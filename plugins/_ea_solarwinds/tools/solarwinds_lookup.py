"""Read one thing from SolarWinds, by name.

The tool argument is a **capability name**, never SWQL text. That is the whole
security design, spelled out in full in `helpers/capabilities.py` and
`helpers/client.py`: there is no argument here that becomes query text
directly, and no method anywhere in this plugin that can write to SolarWinds.

Errors are shaped for the situation that caused them, not passed through raw:
a missing capability's module gets a plain "not enabled here," a
misconfigured plugin gets a plain "ask an administrator," and nothing ever
puts SWIS's own error text or the account's credentials into a transcript.
"""

from __future__ import annotations

from helpers.parallel_tools import user_facing_context
from helpers.tool import Response, Tool

from plugins._ea_solarwinds.helpers import capabilities, client
from plugins._ea_solarwinds.helpers import config as plugin_config
from plugins._ea_solarwinds.helpers import policy


class SolarWindsLookup(Tool):
    async def execute(self, capability: str = "", **kwargs) -> Response:
        capability = str(capability or self.args.get("capability") or "").strip()

        if not plugin_config.is_configured():
            return Response(
                message=(
                    "SolarWinds is not configured on this instance. An "
                    "administrator sets the connection under Settings."
                ),
                break_loop=False,
            )

        try:
            built = capabilities.build(capability, dict(self.args or {}))
        except capabilities.UnknownCapability as error:
            return Response(message=str(error), break_loop=False)
        except capabilities.CapabilityError as error:
            return Response(message=str(error), break_loop=False)

        surface = user_facing_context(self.agent)
        try:
            policy.check_and_record(surface.id)
        except policy.RateLimited as error:
            return Response(message=str(error), break_loop=False)

        try:
            rows = client.query(built.swql, built.params)
        except client.CapabilityUnavailable:
            return Response(
                message=(
                    f"'{capability}' isn't enabled on this SolarWinds instance "
                    "-- the module it needs isn't installed or licensed here."
                ),
                break_loop=False,
            )
        except client.NotConfigured as error:
            return Response(message=str(error), break_loop=False)
        except client.ConnectionFailed as error:
            return Response(message=f"Could not read from SolarWinds: {error}", break_loop=False)

        return Response(message=_format(capability, rows), break_loop=False)


def _format(capability: str, rows: list[dict]) -> str:
    if not rows:
        return f"'{capability}' returned no results."

    lines = [f"{capability}: {len(rows)} result(s)."]
    if capability == "device_config":
        rows = [{**row, "Config": capabilities.cut_config(row.get("Config"))} for row in rows]
        if len(rows) >= capabilities.MAX_CONFIGS:
            lines.append(
                f"At most {capabilities.MAX_CONFIGS} per call, each cut to {capabilities.CONFIG_LINES} lines. "
                f"For older ones call again with before={rows[-1].get('DownloadTime')}."
            )
    for row in rows:
        lines.append(
            "- " + "; ".join(f"{key}: {value}" for key, value in row.items() if value not in (None, ""))
        )
    return "\n".join(lines)
