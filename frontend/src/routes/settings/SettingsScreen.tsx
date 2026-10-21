/**
 * Settings: an index beside a pane.
 *
 * What this replaced was one column of eleven cards that grew by one with every
 * plugin, in which the only way to learn whether mail was on or the licence was
 * in grace was to scroll until you found it. Two things change. State moves out
 * of the scroll and onto the index, so the common visit -- a question, not an
 * edit -- is answered without opening anything. And each section gets an
 * address, so `/settings/licence` is a link an install guide can print and a
 * test can assert, rather than a scroll position that documentation can only
 * describe and get wrong.
 *
 * The top-level grouping is still who may change a setting, which the previous
 * screen got right. See `types.ts` for why sub-grouping was added under it, and
 * `registry.tsx` for the rules a section's chip has to follow.
 *
 * Loading is deliberately one gate rather than eleven. The shared payloads are
 * fetched once here, and no section body is mounted until they have settled --
 * a section that mounted against a half-loaded payload would capture empty
 * initial form state and then quietly compare every later edit against it.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { getSettings } from "@/api/admin";
import { getAzureConfig } from "@/api/azure";
import { getNetworkConfig } from "@/api/networkAutomation";
import { getSiemForwardConfig } from "@/api/siemForward";
import { getSolarWindsConfig } from "@/api/solarwinds";
import { PERMISSIONS } from "@/api/types";
import { Button, Card, Tag } from "@/components";
import { useCan, useSession } from "@/lib/session";
import { useAsync, type AsyncState } from "@/lib/useAsync";
import { collectAttention, matchesQuery, visibleSections } from "./filter";
import { ScopeBanner, SectionError, SectionLoading } from "./parts";
import { SECTIONS } from "./registry";
import { SECTION_GROUPS, type SettingsContext, type SettingsSection } from "./types";
import styles from "./settings.module.css";

export function SettingsScreen() {
  const session = useSession();
  const location = useLocation();
  const canAdminister = useCan(PERMISSIONS.settingsManage);
  const canManagePlugins = useCan(PERMISSIONS.pluginsManage);

  // Each source is fetched only when the user may read it, so a member never
  // makes a request that is certain to be refused.
  const settings = useAsync(
    async () => (canAdminister ? await getSettings() : null),
    [canAdminister],
  );
  const solarwinds = useAsync(
    async () => (canManagePlugins ? await getSolarWindsConfig() : null),
    [canManagePlugins],
  );
  const siem = useAsync(
    async () => (canManagePlugins ? await getSiemForwardConfig() : null),
    [canManagePlugins],
  );
  const network = useAsync(
    async () => (canManagePlugins ? await getNetworkConfig() : null),
    [canManagePlugins],
  );

  const azure = useAsync(
    async () => (canManagePlugins ? await getAzureConfig() : null),
    [canManagePlugins],
  );

  // Only the core payload blocks the screen. Personal and Administration
  // sections depend on `settings` alone, so they must not wait on a plugin's
  // own endpoint -- `/settings/appearance` opening instantly regardless of
  // whether SolarWinds answered was the point.
  const coreLoading = settings.state.status === "loading";
  const pluginsLoading =
    solarwinds.state.status === "loading" ||
    siem.state.status === "loading" ||
    network.state.status === "loading" ||
    azure.state.status === "loading";

  // Only `settings` is worth blocking the screen for. If a plugin's own
  // endpoint is down, its chip is simply absent and the rest still works.
  const fatal = settings.state.status === "error" ? settings.state.error : null;

  const ctx: SettingsContext = useMemo(
    () => ({
      session,
      settings: settings.state.status === "ready" ? settings.state.data : null,
      solarwinds: solarwinds.state.status === "ready" ? solarwinds.state.data : null,
      siem: siem.state.status === "ready" ? siem.state.data : null,
      network: network.state.status === "ready" ? network.state.data : null,
      azure: azure.state.status === "ready" ? azure.state.data : null,
      reload: () => {
        settings.reload();
        solarwinds.reload();
        siem.reload();
        network.reload();
        azure.reload();
      },
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [session, settings.state, solarwinds.state, siem.state, network.state, azure.state],
  );

  // A plugin section is judged visible only once its own payload settles;
  // otherwise it would flicker out and back in as the fetch lands.
  const available = visibleSections(SECTIONS, ctx, new Set(session.permissions), pluginsLoading);

  const activeId = location.pathname.replace(/^\/settings\/?/, "").split("/")[0] || null;
  const active = available.find((section) => section.id === activeId) ?? null;

  // Each section reads one shared payload. Handing the pane that payload's own
  // async state lets a plugin fetch fail into a retry inside the section rather
  // than removing the section from the screen with no way back.
  const sourceFor = (section: SettingsSection | null) => {
    switch (section?.id) {
      case "solarwinds":
        return solarwinds;
      case "network-automation":
        return network;
      case "siem-forwarding":
        return siem;
      case "azure":
        return azure;
      default:
        return settings;
    }
  };

  return (
    <div className={styles.layout} data-view={activeId ? "section" : "index"}>
      <SectionIndex sections={available} ctx={ctx} activeId={activeId} loading={coreLoading} />
      <div className={styles.pane}>
        <div className={styles.paneScroll}>
          {fatal ? (
            <Card title="Settings" danger>
              <p style={{ padding: "var(--space-3) 0" }}>{fatal.message}</p>
              <Button onClick={settings.reload}>Retry</Button>
            </Card>
          ) : coreLoading ? (
            <Card title={active ? active.title : "Settings"}>
              <p style={{ padding: "var(--space-3) 0", color: "var(--ink-3)" }}>Loading…</p>
            </Card>
          ) : active ? (
            <SectionPane section={active} ctx={ctx} source={sourceFor(active)} />
          ) : activeId ? (
            <NotFound id={activeId} />
          ) : (
            <Overview sections={available} ctx={ctx} />
          )}
        </div>
      </div>
    </div>
  );
}

/* ---------- the index ---------- */

function SectionIndex({
  sections,
  ctx,
  activeId,
  loading,
}: {
  sections: SettingsSection[];
  ctx: SettingsContext;
  activeId: string | null;
  loading: boolean;
}) {
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  // "/" is the convention for jump-to-search, and the reason a long list of
  // sections stays usable however many plugins add one.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || target?.isContentEditable) return;
      event.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const needle = query.trim();
  const matching = sections.filter((section) => matchesQuery(section, needle));
  const attention = loading ? [] : collectAttention(sections, ctx);

  // Highlight the matched run in a title so a filtered list shows *why* each
  // row survived, rather than leaving the reader to spot it. Titles that match
  // only via summary or keywords simply render unmarked.
  const highlight = (text: string) => {
    if (!needle) return text;
    const at = text.toLowerCase().indexOf(needle.toLowerCase());
    if (at === -1) return text;
    return (
      <>
        {text.slice(0, at)}
        <mark className={styles.mark}>{text.slice(at, at + needle.length)}</mark>
        {text.slice(at + needle.length)}
      </>
    );
  };

  return (
    <nav className={styles.index} aria-label="Settings sections">
      <div className={styles.indexHead}>
        <div className={styles.search}>
          <input
            ref={searchRef}
            type="search"
            className={styles.searchInput}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter settings…"
            aria-label="Filter settings"
            autoComplete="off"
          />
          <span className={styles.searchKey} aria-hidden="true">
            /
          </span>
        </div>
      </div>

      {attention.length > 0 && !needle && (
        <div className={styles.attention}>
          <div className={styles.attentionLabel}>Needs attention</div>
          {attention.map(({ section, text }) => (
            <Link key={section.id} to={`/settings/${section.id}`} className={styles.attentionItem}>
              {text}
            </Link>
          ))}
        </div>
      )}

      <div className={styles.indexScroll}>
        {matching.length === 0 && (
          <div className={styles.empty}>Nothing matches “{query}”.</div>
        )}
        {SECTION_GROUPS.map((group) => {
          const items = matching.filter((section) => section.group === group);
          if (!items.length) return null;
          return (
            <div key={group}>
              <div className={styles.group}>
                {group}
                <span className={styles.groupCount}>{items.length}</span>
              </div>
              {items.map((section) => {
                const chip = section.chip?.(ctx) ?? null;
                const isActive = section.id === activeId;
                return (
                  <Link
                    key={section.id}
                    to={`/settings/${section.id}`}
                    className={`${styles.item} ${isActive ? styles.itemActive : ""}`}
                    aria-current={isActive ? "page" : undefined}
                  >
                    <span className={styles.itemLabel}>{highlight(section.title)}</span>
                    {chip && <Tag tone={chip.tone}>{chip.text}</Tag>}
                  </Link>
                );
              })}
            </div>
          );
        })}
      </div>
    </nav>
  );
}

/* ---------- the pane ---------- */

function SectionPane({
  section,
  ctx,
  source,
}: {
  section: SettingsSection;
  ctx: SettingsContext;
  source: { state: AsyncState<unknown>; reload: () => void };
}) {
  const Body = section.Component;
  return (
    <>
      <div className={styles.paneHead}>
        <Link to="/settings" className={styles.back}>
          ← All settings
        </Link>
        <div className={styles.crumb}>
          Settings <span aria-hidden="true">›</span> {section.group}
          <span aria-hidden="true">·</span>
          <span className={styles.crumbPath}>/settings/{section.id}</span>
        </div>
        <h1 className={styles.paneTitle}>{section.title}</h1>
        <p className={styles.paneDesc}>{section.summary}</p>
      </div>
      <ScopeBanner group={section.group} />
      {source.state.status === "loading" ? (
        <SectionLoading title={section.title} />
      ) : source.state.status === "error" ? (
        <SectionError
          title={section.title}
          message={source.state.error.message}
          onRetry={source.reload}
        />
      ) : (
        <Body ctx={ctx} />
      )}
    </>
  );
}

/**
 * What `/settings` itself shows: every section and its state on one screen.
 *
 * Not a placeholder for "nothing selected". It is the answer to the question
 * that used to require scrolling the whole page -- what is configured here, and
 * is any of it unhappy.
 */
function Overview({ sections, ctx }: { sections: SettingsSection[]; ctx: SettingsContext }) {
  const attention = collectAttention(sections, ctx);
  const canAdminister = useCan(PERMISSIONS.settingsManage);

  return (
    <>
      <div className={styles.paneHead}>
        <h1 className={styles.paneTitle}>Settings</h1>
        <p className={styles.paneDesc}>
          Personal preferences apply to your account alone. Everything else applies to
          everyone on this instance, and its endpoints reject anyone without the
          permission — whatever the interface chooses to render.
        </p>
      </div>

      {/* Said once, here, rather than as a card per hidden section. The
          sections themselves are not offered, on the same reasoning the main
          navigation follows: a destination that 403s on arrival is worse than
          no destination. */}
      {!canAdminister && (
        <Card title="Platform configuration" description="Not available to your role.">
          <p
            style={{
              padding: "var(--space-3) 0",
              margin: 0,
              fontSize: "var(--text-sm)",
              color: "var(--ink-2)",
            }}
          >
            Settings that affect everyone on this instance are changed by an
            administrator. Ask one if you need something changed.
          </p>
        </Card>
      )}

      {attention.length > 0 && (
        <Card title="Needs attention" description="States worth acting on today.">
          {attention.map(({ section, text }) => (
            <div
              key={section.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-3)",
                padding: "11px 0",
                borderBottom: "1px solid var(--line)",
              }}
            >
              <span style={{ flex: 1, fontSize: "var(--text-base)" }}>{text}</span>
              <Link to={`/settings/${section.id}`} style={{ textDecoration: "none" }}>
                <span
                  style={{
                    fontSize: "var(--text-sm)",
                    fontWeight: 600,
                    color: "var(--accent)",
                    whiteSpace: "nowrap",
                  }}
                >
                  {section.title} →
                </span>
              </Link>
            </div>
          ))}
        </Card>
      )}

      {SECTION_GROUPS.map((group) => {
        const items = sections.filter((section) => section.group === group);
        if (!items.length) return null;
        return (
          <div key={group} style={{ display: "grid", gap: "var(--space-3)" }}>
            <div className={styles.group} style={{ padding: 0 }}>
              {group}
            </div>
            <div className={styles.overviewGrid}>
              {items.map((section) => {
                const chip = section.chip?.(ctx) ?? null;
                return (
                  <Link
                    key={section.id}
                    to={`/settings/${section.id}`}
                    className={styles.overviewCard}
                  >
                    <div className={styles.overviewTop}>
                      <span className={styles.overviewTitle}>{section.title}</span>
                      {chip && <Tag tone={chip.tone}>{chip.text}</Tag>}
                    </div>
                    <span className={styles.overviewSummary}>{section.summary}</span>
                  </Link>
                );
              })}
            </div>
          </div>
        );
      })}
    </>
  );
}

function NotFound({ id }: { id: string }) {
  return (
    <Card title="No such setting">
      <p style={{ padding: "var(--space-3) 0", fontSize: "var(--text-sm)", color: "var(--ink-2)" }}>
        There is no settings section called <code>{id}</code>, or it needs a permission this
        account does not hold.
      </p>
      <p style={{ paddingBottom: "var(--space-3)", margin: 0 }}>
        <Link to="/settings" style={{ color: "var(--accent)", fontWeight: 600 }}>
          All settings
        </Link>
      </p>
    </Card>
  );
}

