/**
 * Plugins.
 *
 * One list, not the two the inherited interface split across the Agent and
 * External settings tabs. Enabling a plugin changes what every user's agent can
 * do, which is why this needs plugins.manage and why it says so.
 *
 * There is no install path: plugin installation was removed deliberately,
 * because a plugin is code and this product executes code. Adding a capability
 * is a change to the repository, not a runtime action.
 */

import { useState } from "react";

import { getPluginDoc, listPlugins, togglePlugin, type PluginListItem } from "@/api/admin";
import {
  Button,
  Card,
  CenteredMessage,
  Drawer,
  EmptyRow,
  LoadFailed,
  Mono,
  ScopeNote,
  SearchInput,
  Select,
  Table,
  Tag,
  Toggle,
  useConfirm,
  useToast,
} from "@/components";
import { Markdown } from "@/lib/Markdown";
import { isSafeHref } from "@/lib/markdown";
import { useAsync } from "@/lib/useAsync";
import { useRowBusy } from "@/lib/useRowBusy";
import { useTableControls } from "@/lib/useTableControls";
import styles from "./shell.module.css";

type SourceFilter = "all" | "bundled" | "custom";

const pluginSearch = (plugin: PluginListItem) => [
  plugin.name,
  plugin.display_name,
  plugin.description,
  plugin.author,
];

interface DocView {
  plugin: PluginListItem;
  doc: "readme" | "license";
}

export function PluginsScreen() {
  const { state, reload } = useAsync(() => listPlugins({ builtin: true, custom: true }), []);
  const rowBusy = useRowBusy();
  const toast = useToast();
  const { confirm } = useConfirm();
  const [source, setSource] = useState<SourceFilter>("all");
  const [doc, setDoc] = useState<DocView | null>(null);

  const plugins = state.status === "ready" ? state.data.plugins ?? [] : [];
  const controls = useTableControls(plugins, {
    search: pluginSearch,
    ...(source === "all"
      ? {}
      : {
          filter: (plugin: PluginListItem) =>
            source === "custom" ? Boolean(plugin.is_custom) : !plugin.is_custom,
        }),
  });

  if (state.status === "loading") return <CenteredMessage title="Loading plugins…" />;
  if (state.status === "error") {
    return (
      <LoadFailed what="plugins" error={state.error} onRetry={reload} />
    );
  }

  // Scoped per plugin so toggling one does not freeze the whole list.
  async function toggle(plugin: PluginListItem) {
    const enable = plugin.toggle_state !== "enabled";
    const name = plugin.display_name || plugin.name;
    // A plugin changes what every user's agent can do, so the switch asks first.
    const ok = await confirm({
      title: `${enable ? "Enable" : "Disable"} ${name}?`,
      body: enable
        ? "Every user's agent gains what this plugin adds."
        : "Every user's agent loses what this plugin adds, including in conversations already open.",
      confirmLabel: enable ? "Enable" : "Disable",
      danger: !enable,
    });
    if (!ok) return;
    await rowBusy.run(plugin.name, async () => {
      try {
        await togglePlugin(plugin.name, enable);
        toast.success(`${plugin.display_name || plugin.name} ${enable ? "enabled" : "disabled"}.`);
        reload();
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : String(caught));
      }
    });
  }

  const bundled = plugins.filter((p) => !p.is_custom).length;
  const custom = plugins.length - bundled;

  return (
    <div className={styles.stack}>
      <ScopeNote>
        <div>
          <strong>Administrator only.</strong> Plugins add tools the agent can
          call, so enabling one changes what every user's agent can do.
        </div>
      </ScopeNote>

      <Card
        title="Installed"
        description={
          plugins.length
            ? `${plugins.length} plugin${plugins.length === 1 ? "" : "s"} · ${bundled} bundled · ${custom} custom`
            : "No plugins were discovered on this instance."
        }
      >
        {plugins.length > 0 ? (
          <>
            <div
              style={{
                display: "flex",
                gap: "var(--space-2)",
                flexWrap: "wrap",
                alignItems: "center",
                padding: "var(--space-3) 0",
              }}
            >
              <SearchInput
                value={controls.query}
                onChange={controls.setQuery}
                placeholder="Search plugins…"
                label="Search plugins"
              />
              <Select
                value={source}
                onChange={(event) => setSource(event.target.value as SourceFilter)}
                aria-label="Filter by source"
              >
                <option value="all">All sources</option>
                <option value="bundled">Bundled ({bundled})</option>
                <option value="custom">Custom ({custom})</option>
              </Select>
            </div>
            <Table>
              <thead>
                <tr>
                  <th>Plugin</th>
                  <th>Description</th>
                  <th>Docs</th>
                  <th>State</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {controls.rows.length === 0 ? (
                  <EmptyRow colSpan={5}>No plugins match your search.</EmptyRow>
                ) : (
                  controls.rows.map((plugin) => (
                    <PluginRow
                      key={plugin.name}
                      plugin={plugin}
                      busy={rowBusy.isBusy(plugin.name)}
                      onToggle={() => void toggle(plugin)}
                      onOpenDoc={(which) => setDoc({ plugin, doc: which })}
                    />
                  ))
                )}
              </tbody>
            </Table>
          </>
        ) : (
          <p style={{ padding: "var(--space-3) 0", color: "var(--ink-3)", fontSize: "var(--text-sm)" }}>
            No plugin directories with a <Mono>plugin.yaml</Mono> were found in{" "}
            <Mono>usr/plugins/</Mono> or <Mono>plugins/</Mono>.
          </p>
        )}
      </Card>

      <Card title="Adding a capability" description="Why there is no install button.">
        <p style={{ padding: "var(--space-3) 0", fontSize: "var(--text-sm)", color: "var(--ink-2)", margin: 0, maxWidth: "62ch" }}>
          Installing plugins from a ZIP, a Git URL or a community index was removed
          from this product. A plugin is executable code running inside an agent
          that already executes code, so adding one is a change to the repository
          with the review that implies — not something to do at runtime from a
          browser.
        </p>
      </Card>

      {doc && (
        <DocDrawer
          plugin={doc.plugin}
          doc={doc.doc}
          onClose={() => setDoc(null)}
          onError={(message) => {
            toast.error(message);
            setDoc(null);
          }}
        />
      )}
    </div>
  );
}

function PluginRow({
  plugin,
  busy,
  onToggle,
  onOpenDoc,
}: {
  plugin: PluginListItem;
  busy: boolean;
  onToggle: () => void;
  onOpenDoc: (doc: "readme" | "license") => void;
}) {
  return (
    <tr>
      <td>
        <strong>{plugin.display_name || plugin.name}</strong>
        <div style={{ color: "var(--ink-3)" }}>
          <Mono>{plugin.name}</Mono>
          {plugin.version ? ` · ${plugin.version}` : ""}
          {plugin.is_custom ? " · custom" : " · bundled"}
          {plugin.author ? ` · ${plugin.author}` : ""}
        </div>
      </td>
      <td style={{ maxWidth: "42ch", color: "var(--ink-2)" }}>{plugin.description || "—"}</td>
      <td style={{ whiteSpace: "nowrap" }}>
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {plugin.has_readme && (
            <Button onClick={() => onOpenDoc("readme")}>README</Button>
          )}
          {plugin.has_license && (
            <Button onClick={() => onOpenDoc("license")}>Licence</Button>
          )}
          {plugin.repo && isSafeHref(plugin.repo) && (
            <a
              href={plugin.repo}
              target="_blank"
              rel="noopener noreferrer nofollow"
              style={{ alignSelf: "center", fontSize: "var(--text-sm)", color: "var(--accent)" }}
            >
              Repository
            </a>
          )}
          {!plugin.has_readme && !plugin.has_license && !(plugin.repo && isSafeHref(plugin.repo)) && (
            <span style={{ color: "var(--ink-3)" }}>—</span>
          )}
        </div>
      </td>
      <td>
        {plugin.always_enabled ? (
          <Tag tone="ok">always on</Tag>
        ) : plugin.toggle_state === "enabled" ? (
          <Tag tone="ok">enabled</Tag>
        ) : (
          <Tag tone="muted">disabled</Tag>
        )}
      </td>
      <td style={{ textAlign: "right" }}>
        <Toggle
          checked={plugin.toggle_state === "enabled" || Boolean(plugin.always_enabled)}
          disabled={Boolean(plugin.always_enabled) || busy}
          label={`Enable ${plugin.name}`}
          onChange={onToggle}
        />
      </td>
    </tr>
  );
}

/**
 * README/LICENSE for one plugin, fetched on open via api/plugins.py::_get_doc.
 *
 * This is the only in-app "deep link" a plugin can offer in this fork: the
 * webui/ config and main screens were removed (decision 0015), but the shipped
 * Markdown docs still describe what a capability does before an administrator
 * enables it for every user.
 */
function DocDrawer({
  plugin,
  doc,
  onClose,
  onError,
}: {
  plugin: PluginListItem;
  doc: "readme" | "license";
  onClose: () => void;
  onError: (message: string) => void;
}) {
  const { state } = useAsync(() => getPluginDoc(plugin.name, doc), [plugin.name, doc]);

  if (state.status === "error") {
    onError(state.error.message);
    return null;
  }

  const label = doc === "readme" ? "README" : "Licence";
  return (
    <Drawer title={`${plugin.display_name || plugin.name} · ${label}`} onClose={onClose}>
      {state.status === "loading" ? (
        <p style={{ color: "var(--ink-3)" }}>Loading {label.toLowerCase()}…</p>
      ) : doc === "license" ? (
        <pre style={{ whiteSpace: "pre-wrap", fontSize: "var(--text-sm)", margin: 0 }}>
          {state.data.content}
        </pre>
      ) : (
        <div style={{ maxWidth: "70ch" }}>
          <Markdown text={state.data.content} />
        </div>
      )}
    </Drawer>
  );
}
