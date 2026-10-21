import re

from helpers.extension import Extension
from helpers.secrets import ALIAS_PATTERN, get_secrets_manager, tool_resolves_secrets


class UnmaskToolSecrets(Extension):

    async def execute(self, tool_args=None, tool_name: str = "", **kwargs):
        if not self.agent or not tool_args:
            return

        if not tool_resolves_secrets(tool_name):
            # Left as written: resolved, a prompt injection could have the agent
            # put a secret into a URL, an email or a scheduled task.
            if any(isinstance(v, str) and re.search(ALIAS_PATTERN, v) for v in tool_args.values()):
                self.agent.hist_add_warning(
                    f"Secret aliases are resolved only for code execution and the text editor; `{tool_name}` received them unresolved."
                )
            return

        secrets_mgr = get_secrets_manager(self.agent.context)

        # Unmask placeholders in args for actual tool execution
        for k, v in tool_args.items():
            if isinstance(v, str):
                tool_args[k] = secrets_mgr.replace_placeholders(v)
