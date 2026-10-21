# Rezztor EAgent Configuration

Main configuration is edited in Settings and kept in the PostgreSQL database; there is no settings file. Settings are administrator-only: they include model provider credentials.

LLM roles:
- `chat_llm`: primary reasoning and tool use
- `utility_llm`: summaries, memory queries, compression, filtering
- `embedding_llm`: vector embeddings for memory and knowledge

Accounts are rows in the PostgreSQL database; there is no accounts file. Administrators create accounts and assign one of three roles: `admin`, `power`, or `user`.

Profiles live in `agents/<profile>/`; user profiles live in `usr/agents/<profile>/`. Profiles override prompt fragments without changing the framework.

Plugins live in `plugins/` and `usr/plugins/`. Each plugin has a `plugin.yaml`; activation can be global or scoped to projects/profiles.

Projects isolate workdir, memory/knowledge scope, custom instructions, secrets, MCP config, and repositories.

Environment settings can use `REZZTOR_SET_<setting_name>=<value>`.
