# Rezztor EAgent Capabilities

Rezztor EAgent can:
- run terminal and code execution tools in a separate runner container, as the signed-in user's own unprivileged account
- read, write, and patch files with text editor tools
- search the web with the search tool (there is no browser tool)
- create and query document artifacts
- save, load, and forget memories
- schedule tasks
- call subordinate agents
- use MCP and A2A integrations when configured

Capabilities also depend on the signed-in user's role. Standard users have chat, memory, and knowledge only; code execution, subordinate agents, scheduled tasks, the device inventory and the network tools require a power-user or administrator role.

Important boundary:
- Container tools operate inside the Rezztor EAgent runtime, usually `/eagent/usr/workdir`.
- Do not confuse host paths with container paths.

Capabilities otherwise depend on enabled plugins, settings, model quality, permissions, and active project context.
