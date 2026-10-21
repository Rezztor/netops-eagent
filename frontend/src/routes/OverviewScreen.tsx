/**
 * Home: what needs doing, for the person looking at it.
 *
 * An administrator gets the setup checklist -- model, licence, executor,
 * devices, mail, a first engineer -- each item computed from an API the
 * product already has, each linking to where it is done. Anyone who can
 * approve network changes sees what waits for them; anyone who chats sees
 * their recent conversations. Access is said as the role's name and plain
 * capabilities, not permission codes.
 */

import { Link } from "react-router-dom";

import { getSettings, listUsers } from "@/api/admin";
import { getInventorySummary } from "@/api/inventorySummary";
import { getModelConfig } from "@/api/models";
import { getApprovalsWaiting, getNetworkConfig } from "@/api/networkAutomation";
import { PERMISSIONS } from "@/api/types";
import { Card, Mono, Row, Tag } from "@/components";
import { useRealtime } from "@/lib/realtime";
import { useCan, useSession } from "@/lib/session";
import { useAsync } from "@/lib/useAsync";
import { displayVersion } from "./AppShell";
import styles from "./shell.module.css";

/** What each permission lets a person do, in words they would use. */
const CAPABILITIES: Record<string, string> = {
  [PERMISSIONS.chatUse]: "Chat with the agent",
  [PERMISSIONS.toolCodeExecution]: "Let the agent run code",
  [PERMISSIONS.toolSubordinates]: "Let the agent hand work to helper agents",
  [PERMISSIONS.toolNetwork]: "Use the network tools",
  [PERMISSIONS.networkView]: "See network jobs and what ran",
  [PERMISSIONS.networkApprove]: "Approve network changes",
  [PERMISSIONS.inventoryView]: "Read the device inventory",
  [PERMISSIONS.inventoryManage]: "Propose inventory changes",
  [PERMISSIONS.inventoryApprove]: "Approve inventory changes",
  [PERMISSIONS.inventoryPlaces]: "Manage sites and buildings",
  [PERMISSIONS.schedulerManage]: "Schedule unattended work",
  [PERMISSIONS.usersManage]: "Manage accounts and roles",
  [PERMISSIONS.settingsManage]: "Change models and instance settings",
  [PERMISSIONS.pluginsManage]: "Manage plugins",
  [PERMISSIONS.auditViewAll]: "See everyone's activity",
};

export function capabilitiesOf(permissions: readonly string[]): string[] {
  return permissions.map((p) => CAPABILITIES[p]).filter((c): c is string => Boolean(c));
}

export function OverviewScreen() {
  const session = useSession();
  const isAdmin = useCan(PERMISSIONS.settingsManage);
  const canApprove = useCan(PERMISSIONS.networkApprove);
  const canChat = useCan(PERMISSIONS.chatUse);

  return (
    <div className={styles.stack}>
      <div className={styles.pageHead}>
        <h2 className={styles.pageHeadTitle}>Welcome, {session.user.display_name.split(" ")[0]}</h2>
        <p className={styles.pageHeadDesc}>You are signed in as {session.user.role_title}.</p>
      </div>

      {isAdmin && <GetStarted />}
      {canApprove && <WaitingApprovals />}
      {canChat && <RecentChats />}

      <Card title="Your access" description="What your role lets you do here.">
        <Row label="Role">
          <Tag tone={isAdmin ? "accent" : "muted"}>{session.user.role_title}</Tag>
        </Row>
        <ul className={styles.plainList}>
          {capabilitiesOf(session.permissions).map((phrase) => (
            <li key={phrase}>{phrase}</li>
          ))}
        </ul>
      </Card>

      <Card title="Platform">
        <Row label="Version">
          <Mono>{displayVersion(session.version.version) || "unknown"}</Mono>
        </Row>
        <Row label="Built">
          <span style={{ color: "var(--ink-3)" }}>{session.version.commit_time || "unknown"}</span>
        </Row>
        {session.runtime.is_development && (
          <Row label="Mode" help="Development mode opens paths a deployment should not have.">
            <Tag tone="warn">development</Tag>
          </Row>
        )}
      </Card>
    </div>
  );
}

/* ---------- administrator: the setup checklist ---------- */

export interface SetupStep {
  label: string;
  to: string;
  /** null when it could not be checked: said so, never shown as done. */
  done: boolean | null;
}

/** One check against an API the product already has. A failed request is "unknown", not "not done". */
async function check(test: () => Promise<boolean>): Promise<boolean | null> {
  try {
    return await test();
  } catch {
    return null;
  }
}

export async function loadSetupSteps(licensed: boolean): Promise<SetupStep[]> {
  const [model, executor, devices, mail, engineer] = await Promise.all([
    check(async () => {
      const config = await getModelConfig();
      const provider = config.config.chat_model?.provider ?? "";
      return Boolean(config.model_configured && provider && config.api_key_status[provider]);
    }),
    check(async () => {
      const state = await getNetworkConfig();
      return Boolean(state.config.configured && state.config.executor_token_set);
    }),
    check(async () => {
      const summary = await getInventorySummary();
      return Boolean(summary.ok && summary.devices > 0);
    }),
    check(async () => Boolean((await getSettings()).settings["smtp_enabled"])),
    check(async () => (await listUsers()).users.length > 1),
  ]);
  return [
    { label: "Choose a chat model and give its provider a key", to: "/models", done: model },
    { label: "Activate the licence", to: "/settings/licence", done: licensed },
    { label: "Connect network automation (executor address and token)", to: "/settings/network-automation", done: executor },
    { label: "Import your devices", to: "/inventory", done: devices },
    { label: "Set up email for reminders and password resets", to: "/settings/email", done: mail },
    { label: "Invite your first engineer", to: "/users", done: engineer },
  ];
}

function GetStarted() {
  const session = useSession();
  const licensed = session.licence?.state === "licensed";
  const { state } = useAsync(() => loadSetupSteps(licensed), [licensed]);
  if (state.status !== "ready") {
    return <Card title="Get started" description={state.status === "loading" ? "Checking…" : "Could not check."} />;
  }
  const steps = state.data;
  const done = steps.filter((s) => s.done === true).length;
  if (done === steps.length) {
    return <Card title="Get started" description="Everything is set up." />;
  }
  return (
    <Card title="Get started" description={`${done} of ${steps.length} done`}>
      <ol className={styles.checklist}>
        {steps.map((step) => (
          <li key={step.to}>
            <span className={styles.checkMark} data-done={String(step.done)} aria-hidden="true">
              {step.done === true ? "✓" : step.done === null ? "?" : "○"}
            </span>
            <Link to={step.to}>{step.label}</Link>
            <span className="visually-hidden">
              {step.done === true ? " (done)" : step.done === null ? " (could not check)" : " (not done)"}
            </span>
          </li>
        ))}
      </ol>
    </Card>
  );
}

/* ---------- engineers: what waits, and where they were ---------- */

function WaitingApprovals() {
  // The Approvals page's own list: only what this person may sign, Azure changes included.
  const { state } = useAsync(() => getApprovalsWaiting(), []);
  if (state.status !== "ready") return null;
  const changes = state.data;
  return (
    <Card
      title="Waiting for approval"
      description={
        changes.length
          ? `${changes.length} change${changes.length === 1 ? "" : "s"}`
          : "Nothing is waiting for you."
      }
      actions={changes.length ? <Link to="/approvals">Open Approvals</Link> : undefined}
    >
      {changes.length > 0 && (
        <ul className={styles.plainList}>
          {changes.slice(0, 5).map((change) => (
            <li key={change.id}>
              <Link to={`/approvals?change=${encodeURIComponent(change.id)}`}>{change.summary || change.playbook_name}</Link>
              {change.requested_by_name ? <span style={{ color: "var(--ink-3)" }}> · {change.requested_by_name}</span> : null}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function RecentChats() {
  const { state } = useRealtime();
  // No snapshot yet (or no live connection): saying "None yet" would be a guess.
  if (!state) return null;
  const when = (c: { last_message?: string; created_at?: string }) => String(c.last_message ?? c.created_at ?? "");
  const contexts = [...(state?.contexts ?? [])].sort((a, b) => when(b).localeCompare(when(a))).slice(0, 5);
  return (
    <Card
      title="Recent conversations"
      description={contexts.length ? undefined : "None yet."}
      actions={<Link to="/chat">Open Chat</Link>}
    >
      {contexts.length > 0 && (
        <ul className={styles.plainList}>
          {contexts.map((context) => (
            <li key={context.id}>
              <Link to={`/chat?ctx=${encodeURIComponent(context.id)}`}>{context.name || "Untitled conversation"}</Link>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}
