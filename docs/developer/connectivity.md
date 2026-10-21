# Connectivity

This page helps you choose the right connection path.

For source-linked architecture and endpoint internals, use
[DeepWiki for Rezztor EAgent](https://deepwiki.com/Rezztor/netops-eagent). The local
docs should not duplicate the full connectivity architecture.

## Choose The Right Path

| Need | Start here |
| --- | --- |
| Let Rezztor EAgent work on your host files, shell, or browser | EAgent CLI Connector |
| Add a third-party tool through MCP | [MCP Setup](../guides/mcp-setup.md) |
| Let another agent talk to Rezztor EAgent | [A2A Setup](../guides/a2a-setup.md) |
| Add an external API for one workflow | [API Integration](../guides/api-integration.md) |
| Study API, MCP, or A2A internals | [DeepWiki](https://deepwiki.com/Rezztor/netops-eagent) |

## External API Basics

You can find your API token in Rezztor EAgent under **Settings > External Services**.

Common external endpoints include:

| Endpoint | Use it for |
| --- | --- |
| `POST /api_message` | Send a message to Rezztor EAgent. |
| `GET/POST /api_log_get` | Read chat logs. |
| `POST /api_terminate_chat` | Stop a running chat. |
| `POST /api_reset_chat` | Reset a chat. |
| `POST /api_files_get` | Retrieve files. |

External API calls use the `X-API-KEY` header.

> [!TIP]
> For exact request and response details, check the current source or the
> matching DeepWiki page. That keeps the API reference tied to the code that is
> actually running.

## MCP And A2A

Use MCP when you want Rezztor EAgent to call tools from another app or service.

Use A2A when you want another agent to talk to Rezztor EAgent as a collaborator.

Both use the same Rezztor EAgent instance and can be project-aware when configured
that way.

## Related

- EAgent CLI Connector
- [MCP Setup](../guides/mcp-setup.md)
- [A2A Setup](../guides/a2a-setup.md)
- [API Integration](../guides/api-integration.md)
- [DeepWiki for Rezztor EAgent](https://deepwiki.com/Rezztor/netops-eagent)
