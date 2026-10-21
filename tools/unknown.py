from helpers import modules
from helpers.tool import Tool, Response


class Unknown(Tool):
    async def execute(self, **kwargs):
        # Loaded as the extension it is, not with an `import`: its build_prompt
        # is an extension point, whose folder comes from the module's name, and
        # a module that is reached both ways has two names
        # (tests/test_compiled_modules.py).
        tools_prompt = modules.import_module("extensions/python/system_prompt/_11_tools_prompt.py")
        tools = await tools_prompt.build_prompt(self.agent)
        return Response(
            message=self.agent.read_prompt(
                "fw.tool_not_found.md", tool_name=self.name, tools_prompt=tools
            ),
            break_loop=False,
        )
