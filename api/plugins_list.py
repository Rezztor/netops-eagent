from helpers.api import ApiHandler, Input, Output, Request
from helpers import plugins


class PluginsList(ApiHandler):
    async def process(self, input: Input, request: Request) -> Output:
        # Accept both shapes for backwards compatibility: the flags nested
        # under "filter" (inherited contract) and at the top level (the shape
        # the React frontend sends). Missing flags default to True so the
        # admin list shows both bundled and custom plugins by default.
        filter = input.get("filter") or {}
        if not isinstance(filter, dict):
            filter = {}
        custom = filter.get("custom", input.get("custom", True))
        builtin = filter.get("builtin", input.get("builtin", True))

        plugin_list = plugins.get_enhanced_plugins_list(custom=custom, builtin=builtin)

        return {"ok": True, "plugins": [p.model_dump(mode="json") for p in plugin_list]}
