---
name: eagent-plugin-router
description: Main entry point for all Rezztor EAgent plugin tasks. Routes to specialist skills for creating, reviewing, managing, or debugging plugins. Use when the user mentions plugins, asks how the plugin system works, or wants to build, enable, disable or debug a plugin.
version: 1.0.0
tags: ["plugins", "router", "meta", "create", "review", "manage"]
trigger_patterns:
  - "plugin"
  - "create plugin"
  - "build plugin"
  - "review plugin"
  - "publish plugin"
  - "install plugin"
  - "manage plugin"
  - "plugin hub"
  - "plugin index"
  - "how does the plugin system work"
---

# Rezztor EAgent Plugin Router

## Routing Decision

Classify the user's request and read the appropriate specialist skill immediately.

| User intent | Skill to read |
|---|---|
| Create / build / develop / write a new plugin | Read `/eagent/skills/eagent-create-plugin/SKILL.md` |
| Review / audit / validate / check a plugin | Read `/eagent/skills/eagent-review-plugin/SKILL.md` |
| Install / update / uninstall / remove / browse / scan | Read `/eagent/skills/eagent-manage-plugin/SKILL.md` |
| Plugin not working / crashing / missing / debug / troubleshoot | Read `/eagent/skills/eagent-debug-plugin/SKILL.md` |
| Explain / how does it work / architecture | Answer inline using the overview below |

If intent is ambiguous, ask one question before routing:
> "Are you trying to **create** a new plugin, **review** one, **manage** (enable/disable) plugins, or **debug** a plugin that isn't working?"

This deployment does not install plugins from anywhere: a plugin is code, so adding one is a change to the repository. If the user asks to install or publish a plugin, say so plainly rather than looking for a way around it.

---

## Plugin System Overview (for explain/explore queries)

### Roots and Discovery

Rezztor EAgent discovers plugins from two roots, in priority order:
1. `usr/plugins/<name>/` - user plugins (your custom plugins go here)
2. `plugins/<name>/` - core system plugins (framework-bundled, do not modify)

A plugin is valid when its directory contains a `plugin.yaml`. Directories starting with `.` are skipped.

### Runtime Manifest (`plugin.yaml`)

Every plugin requires a `plugin.yaml` at its root:

```yaml
name: my_plugin              # required by CI for community plugins (^[a-z0-9_]+$)
title: My Plugin             # UI display name
description: What it does.
version: 1.0.0
settings_sections: [agent]   # which Settings tabs show a subsection
per_project_config: false    # enables project-scoped settings/toggle
per_agent_config: false      # enables agent-profile-scoped settings/toggle
always_enabled: false        # forces ON, disables toggle (framework use only)
```

`settings_sections` valid values: `agent`, `external`, `mcp`, `developer`, `backup`. Use `[]` for none.

### What a Plugin Can Provide

| Directory/File | Purpose |
|---|---|
| `api/` | API handlers (`ApiHandler` subclasses) |
| `tools/` | Agent tools (`Tool` subclasses) |
| `extensions/python/<point>/` and `extensions/python/_functions/<module>/<qualname>/<start_or_end>/` | Backend lifecycle hooks and implicit `@extensible` hooks |
| `extensions/webui/<point>/` | HTML/JS injected into UI breakpoints |
| `webui/config.html` | Plugin settings UI |
| `webui/*.html`, `webui/*.js` | Full plugin pages and Alpine stores |
| `hooks.py` | Framework runtime hooks (install, uninstall, pre_update, cache, registration) |
| `execute.py` | User-triggered script (setup, maintenance, repair) |
| `default_config.yaml` | Settings defaults |
| `README.md` | Optional, but expected: it is how the next person understands the plugin |
| `agents/<profile>/agent.yaml` | Plugin-distributed agent profiles |
| `conf/model_providers.yaml` | Add/override model providers |
| `LICENSE` | Optional under `usr/plugins/`; required at the root of a plugin GitHub repo before submitting to the Plugin Index |

For `@extensible` targets, the only valid implicit hook layout is `extensions/python/_functions/<module>/<qualname>/<start|end>/`. The older flattened `extensions/python/<module>_<qualname>_<start|end>/` folders are obsolete.

### Activation

- Global toggle: `.toggle-1` (ON) / `.toggle-0` (OFF) files in the plugin dir
- Scoped toggles (project/agent) available when `per_project_config` or `per_agent_config` is true
- Default: enabled when no toggle file exists
- `always_enabled: true` forces ON and hides controls (reserved for framework)

### Settings Resolution (highest priority first)

1. `project/.eaproj/agents/<profile>/plugins/<name>/config.json`
2. `project/.eaproj/plugins/<name>/config.json`
3. `usr/agents/<profile>/plugins/<name>/config.json`
4. `usr/plugins/<name>/config.json`
5. `plugins/<name>/default_config.yaml`

### Key API Routes

| Route | Purpose |
|---|---|
| `GET /plugins/<name>/<path>` | Serve static plugin assets |
| `POST /api/plugins/<name>/<handler>` | Call plugin API endpoint |
| `POST /api/plugins` | Management (toggle, config, docs) |

### Deep-Dive References

- Architecture + extension points: `/eagent/plugins/AGENTS.md`
- Developer guide: `/eagent/docs/developer/plugins.md`
- Component system: `/eagent/webui/components/AGENTS.md`
- Modal system: `/eagent/webui/js/AGENTS.md` and `/eagent/webui/css/AGENTS.md`
