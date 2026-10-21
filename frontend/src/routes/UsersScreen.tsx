/**
 * Account administration.
 *
 * The guards this screen surfaces are enforced in helpers/users.py, not here:
 * the last active administrator cannot be demoted, disabled or deleted, and no
 * one can delete their own account. The backend refuses; this screen explains
 * why before the user tries, which is the difference between a rule and a
 * mysterious error.
 */

import { useState, type CSSProperties } from "react";

import {
  changePassword,
  createUser,
  deleteUser,
  listUsers,
  updateUser,
  type AdminUser,
  type RoleInfo,
  type UsersListResponse,
} from "@/api/admin";
import type { Role } from "@/api/types";
import {
  Button,
  Card,
  CenteredMessage,
  DateTime,
  Drawer,
  EmptyRow,
  LoadFailed,
  Mono,
  ScopeNote,
  SearchInput,
  Select,
  SortHeader,
  Table,
  Tag,
  TextInput,
  useConfirm,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import { useRowBusy } from "@/lib/useRowBusy";
import { useTableControls } from "@/lib/useTableControls";
import { useSession } from "@/lib/session";
import styles from "./shell.module.css";

const MIN_PASSWORD_LENGTH = 12;

const fieldStyle: CSSProperties = {
  display: "grid",
  gap: "var(--space-1)",
};

const fieldLabelStyle: CSSProperties = {
  fontSize: "var(--text-sm)",
  color: "var(--ink-2)",
};

const fullWidthInput: CSSProperties = {
  width: "100%",
  minWidth: 0,
  boxSizing: "border-box",
};

function ensureOk(result: { ok: boolean; error?: string }, fallback: string) {
  if (!result.ok) throw new Error(result.error?.trim() || fallback);
}

const userSearch = (user: AdminUser) => [
  user.username,
  user.display_name,
  user.email,
  user.role,
];

const USER_COMPARATORS: Record<string, (a: AdminUser, b: AdminUser) => number> = {
  username: (a, b) => (a.display_name || a.username).localeCompare(b.display_name || b.username),
  role: (a, b) => a.role.localeCompare(b.role),
  last_login: (a, b) =>
    new Date(a.last_login ?? 0).getTime() - new Date(b.last_login ?? 0).getTime(),
  created_at: (a, b) => new Date(a.created_at ?? 0).getTime() - new Date(b.created_at ?? 0).getTime(),
};

export function UsersScreen() {
  const { state, reload } = useAsync<UsersListResponse>(() => listUsers(), []);
  const rowBusy = useRowBusy();
  const toast = useToast();
  const { confirm } = useConfirm();
  const session = useSession();
  const [editing, setEditing] = useState<AdminUser | null>(null);

  const users = state.status === "ready" ? state.data.users : [];
  const controls = useTableControls(users, {
    search: userSearch,
    comparators: USER_COMPARATORS,
    initialSort: { key: "username", direction: "asc" },
  });

  if (state.status === "loading") return <CenteredMessage title="Loading users…" />;
  if (state.status === "error") {
    return (
      <LoadFailed what="users" error={state.error} onRetry={reload} />
    );
  }

  const { roles } = state.data;
  const activeAdmins = users.filter((u) => u.role === "admin" && !u.disabled).length;

  // Scoped to one row/action id so an unrelated account stays operable while
  // another is mid-update.
  // Returns whether it worked. The create form needs to know: it used to clear
  // every field the moment it called this, so a rejected create left the admin
  // with a toast and an empty form to retype -- password included.
  async function run(id: string, action: () => Promise<unknown>, success: string): Promise<boolean> {
    let ok = false;
    await rowBusy.run(id, async () => {
      try {
        await action();
        toast.success(success);
        reload();
        ok = true;
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : String(caught));
      }
    });
    return ok;
  }

  return (
    <div className={styles.stack}>
      <ScopeNote>
        <div>
          <strong>Administrator only.</strong> Accounts and roles are managed here;
          the server refuses these changes from anyone else.
        </div>
      </ScopeNote>

      <Card
        title="Accounts"
        description={`${users.length} total · ${activeAdmins} active administrator${activeAdmins === 1 ? "" : "s"}`}
      >
        <div style={{ padding: "var(--space-3) 0" }}>
          <SearchInput
            value={controls.query}
            onChange={controls.setQuery}
            placeholder="Search accounts…"
            label="Search accounts"
          />
        </div>
        <Table>
          <thead>
            <tr>
              <SortHeader columnKey="username" sort={controls.sort} onToggle={controls.toggleSort}>
                User
              </SortHeader>
              <SortHeader columnKey="role" sort={controls.sort} onToggle={controls.toggleSort}>
                Role
              </SortHeader>
              <th>State</th>
              <SortHeader columnKey="last_login" sort={controls.sort} onToggle={controls.toggleSort}>
                Last sign-in
              </SortHeader>
              <SortHeader columnKey="created_at" sort={controls.sort} onToggle={controls.toggleSort}>
                Created
              </SortHeader>
              <th />
            </tr>
          </thead>
          <tbody>
            {controls.rows.length === 0 ? (
              <EmptyRow colSpan={6}>No accounts match your search.</EmptyRow>
            ) : (
              controls.rows.map((user) => (
                <UserRow
                  key={user.id}
                  user={user}
                  roles={roles}
                  busy={rowBusy.isBusy(user.id)}
                  isSelf={user.id === session.user.id}
                  isLastActiveAdmin={user.role === "admin" && !user.disabled && activeAdmins === 1}
                  onEdit={() => setEditing(user)}
                  onChangeRole={async (role) => {
                    // The role's name, not its id: "Make bob a power?" means nothing.
                    const title = roles.find((r) => r.id === role)?.title ?? role;
                    const ok = await confirm({
                      title: `Make ${user.username} ${/^[AEIOU]/i.test(title) ? "an" : "a"} ${title}?`,
                      body: "Their role decides which parts of the product they can reach and what they may do there.",
                      confirmLabel: "Change role",
                    });
                    if (ok) {
                      void run(user.id, () => updateUser({ username: user.username, role }), `${user.username} is now ${title}.`);
                    }
                  }}
                  onToggleDisabled={async () => {
                    // Enabling gives access back and needs no warning; disabling ends it at once.
                    if (!user.disabled) {
                      const ok = await confirm({
                        title: `Disable ${user.username}?`,
                        body: "They are signed out and cannot sign in until an administrator enables the account again. Their chats and files are kept.",
                        confirmLabel: "Disable",
                        danger: true,
                      });
                      if (!ok) return;
                    }
                    void run(
                      user.id,
                      () => updateUser({ username: user.username, disabled: !user.disabled }),
                      `${user.username} ${user.disabled ? "enabled" : "disabled"}.`,
                    );
                  }}
                  onDelete={async () => {
                    const ok = await confirm({
                      title: `Delete ${user.username}?`,
                      body: "Their chats, memory and files are removed too. This cannot be undone.",
                      danger: true,
                      confirmLabel: "Delete",
                    });
                    if (ok) void run(user.id, () => deleteUser(user.username), `${user.username} deleted.`);
                  }}
                />
              ))
            )}
          </tbody>
        </Table>
      </Card>

      <CreateUserCard
        roles={roles}
        busy={rowBusy.isBusy("create")}
        onCreate={(input) => run("create", () => createUser(input), `${input.username} created.`)}
      />

      <Card title="Roles" description="Permissions are the contract; a role is a bundle of them.">
        <Table>
          <thead>
            <tr>
              <th>Role</th>
              <th>Grants</th>
              <th>Permissions</th>
            </tr>
          </thead>
          <tbody>
            {roles.map((role) => (
              <tr key={role.id}>
                <td>
                  <strong>{role.title}</strong>
                  <div style={{ color: "var(--ink-3)" }}>
                    <Mono>{role.id}</Mono>
                  </div>
                </td>
                <td style={{ maxWidth: "40ch" }}>{role.description}</td>
                <td>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                    {role.permissions.map((permission) => (
                      <Tag key={permission} tone="muted">
                        {permission}
                      </Tag>
                    ))}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      </Card>

      {editing && (
        <EditUserDrawer
          user={editing}
          isSelf={editing.id === session.user.id}
          busy={rowBusy.isBusy(editing.id)}
          onClose={() => setEditing(null)}
          run={(action, success) => run(editing.id, action, success)}
        />
      )}
    </div>
  );
}

function UserRow({
  user,
  roles,
  busy,
  isSelf,
  isLastActiveAdmin,
  onEdit,
  onChangeRole,
  onToggleDisabled,
  onDelete,
}: {
  user: AdminUser;
  roles: RoleInfo[];
  busy: boolean;
  isSelf: boolean;
  isLastActiveAdmin: boolean;
  onEdit: () => void;
  onChangeRole: (role: Role) => void;
  onToggleDisabled: () => void;
  onDelete: () => void;
}) {
  // Both are refused by helpers/users.py. Disabling the control and saying why
  // is better than letting someone click it and read a failure.
  const protectedReason = isLastActiveAdmin
    ? "The last active administrator cannot be changed."
    : isSelf
      ? "You cannot delete your own account."
      : null;

  return (
    <tr>
      <td>
        <strong>{user.display_name || user.username}</strong>
        <div style={{ color: "var(--ink-3)" }}>
          <Mono>{user.username}</Mono>
          {user.email ? ` · ${user.email}` : ""}
        </div>
      </td>
      <td>
        <Select
          value={user.role}
          disabled={busy || isLastActiveAdmin}
          title={isLastActiveAdmin ? protectedReason ?? "" : undefined}
          onChange={(event) => onChangeRole(event.target.value as Role)}
          style={{ minWidth: 130 }}
        >
          {roles.map((role) => (
            <option key={role.id} value={role.id}>
              {role.title}
            </option>
          ))}
        </Select>
      </td>
      <td>
        {user.disabled ? (
          <Tag tone="crit">disabled</Tag>
        ) : user.must_change_password ? (
          <Tag tone="warn">must change password</Tag>
        ) : (
          <Tag tone="ok">active</Tag>
        )}
      </td>
      <td style={{ color: "var(--ink-3)" }}>
        {user.last_login ? <DateTime value={user.last_login} /> : "never"}
      </td>
      <td style={{ color: "var(--ink-3)" }}>
        {user.created_at ? <DateTime value={user.created_at} mode="date" /> : "—"}
        {user.created_by ? (
          <div style={{ fontSize: "var(--text-xs)" }}>by {user.created_by}</div>
        ) : null}
      </td>
      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
        <Button disabled={busy} onClick={onEdit}>
          Edit
        </Button>{" "}
        <Button
          disabled={busy || isLastActiveAdmin}
          title={isLastActiveAdmin ? protectedReason ?? "" : undefined}
          onClick={onToggleDisabled}
        >
          {user.disabled ? "Enable" : "Disable"}
        </Button>{" "}
        <Button
          variant="danger"
          disabled={busy || isSelf || isLastActiveAdmin}
          title={protectedReason ?? ""}
          onClick={onDelete}
        >
          Delete
        </Button>
      </td>
    </tr>
  );
}

function EditUserDrawer({
  user,
  isSelf,
  busy,
  onClose,
  run,
}: {
  user: AdminUser;
  isSelf: boolean;
  busy: boolean;
  onClose: () => void;
  run: (action: () => Promise<unknown>, success: string) => Promise<boolean>;
}) {
  const toast = useToast();
  const [displayName, setDisplayName] = useState(user.display_name);
  const [email, setEmail] = useState(user.email);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [generated, setGenerated] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const passwordMismatch = confirmPassword.length > 0 && newPassword !== confirmPassword;
  const passwordTooShort = newPassword.length > 0 && newPassword.length < MIN_PASSWORD_LENGTH;
  // Why this cannot be submitted yet, or null. The button used to be disabled
  // on these conditions, which meant empty fields produced a dead control and no
  // explanation: the two inline messages below both require a non-empty field,
  // so with nothing typed the drawer said nothing at all. Worse for your own
  // account -- fill both new-password boxes correctly, leave "Current password"
  // blank, and every visible message was satisfied while the button stayed dead.
  //
  // There is no <form> here, so the browser cannot report any of it. The button
  // is live and says what is missing instead.
  const passwordBlockedReason: string | null =
    newPassword.length === 0
      ? "Enter a new password."
      : newPassword.length < MIN_PASSWORD_LENGTH
        ? `A password must be at least ${MIN_PASSWORD_LENGTH} characters.`
        : newPassword !== confirmPassword
          ? "The two entries do not match."
          : isSelf && currentPassword.length === 0
            ? "Enter your current password to change your own."
            : null;

  function clearPasswordFields() {
    setCurrentPassword("");
    setNewPassword("");
    setConfirmPassword("");
  }

  async function copyGenerated(value: string) {
    try {
      await navigator.clipboard?.writeText(value);
      setCopied(true);
      toast.success("Password copied.");
    } catch {
      toast.error("Could not copy to the clipboard.");
    }
  }

  return (
    <Drawer title={`Edit ${user.username}`} onClose={onClose}>
      <div style={{ display: "grid", gap: "var(--space-3)" }}>
        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Username</span>
          <Mono>{user.username}</Mono>
        </label>
        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Display name</span>
          <TextInput
            value={displayName}
            disabled={busy}
            style={fullWidthInput}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </label>
        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Email</span>
          <TextInput
            type="email"
            value={email}
            disabled={busy}
            style={fullWidthInput}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <div>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() =>
              void run(async () => {
                const result = await updateUser({
                  username: user.username,
                  display_name: displayName.trim(),
                  email: email.trim(),
                });
                ensureOk(result, "Could not update the account.");
              }, "Account updated.")
            }
          >
            Save changes
          </Button>
        </div>
      </div>

      <div
        style={{
          borderTop: "1px solid var(--line)",
          marginTop: "var(--space-2)",
          paddingTop: "var(--space-4)",
          display: "grid",
          gap: "var(--space-3)",
        }}
      >
        <div>
          <div style={{ fontWeight: 550, color: "var(--ink)" }}>
            {isSelf ? "Update password" : "Set password"}
          </div>
          <p style={{ margin: "4px 0 0", fontSize: "var(--text-sm)", color: "var(--ink-3)" }}>
            {isSelf
              ? "Enter your current password, then choose a new one."
              : "Set a password they must change at next sign-in, or generate one to copy."}
          </p>
        </div>

        {isSelf && (
          <label style={fieldStyle}>
            <span style={fieldLabelStyle}>Current password</span>
            <TextInput
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              disabled={busy}
              style={fullWidthInput}
              onChange={(e) => setCurrentPassword(e.target.value)}
            />
          </label>
        )}
        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>New password</span>
          <TextInput
            type="password"
            autoComplete="new-password"
            value={newPassword}
            disabled={busy}
            minLength={MIN_PASSWORD_LENGTH}
            style={fullWidthInput}
            onChange={(e) => setNewPassword(e.target.value)}
          />
          <span style={{ fontSize: "var(--text-sm)", color: passwordTooShort ? "var(--warn)" : "var(--ink-3)" }}>
            At least {MIN_PASSWORD_LENGTH} characters.
          </span>
        </label>
        <label style={fieldStyle}>
          <span style={fieldLabelStyle}>Confirm new password</span>
          <TextInput
            type="password"
            autoComplete="new-password"
            value={confirmPassword}
            disabled={busy}
            style={fullWidthInput}
            onChange={(e) => setConfirmPassword(e.target.value)}
          />
          {passwordMismatch ? (
            <span style={{ fontSize: "var(--text-xs)", color: "var(--warn)" }}>
              The two entries do not match.
            </span>
          ) : null}
        </label>

        <div style={{ display: "flex", flexWrap: "wrap", gap: "var(--space-2)" }}>
          <Button
            variant="primary"
            disabled={busy}
            onClick={() => {
              if (passwordBlockedReason) {
                toast.error(passwordBlockedReason);
                return;
              }
              if (isSelf) {
                void run(async () => {
                  const result = await changePassword({
                    current_password: currentPassword,
                    new_password: newPassword,
                  });
                  ensureOk(result, "Could not update the password.");
                  clearPasswordFields();
                  setGenerated(null);
                  setCopied(false);
                }, "Password updated.");
                return;
              }
              void run(async () => {
                const result = await updateUser({
                  username: user.username,
                  password: newPassword,
                });
                ensureOk(result, "Could not set the password.");
                clearPasswordFields();
                setGenerated(null);
                setCopied(false);
              }, "Password set. They must change it at next sign-in.");
            }}
          >
            {isSelf ? "Update password" : "Set password"}
          </Button>
          {!isSelf && (
            <Button
              disabled={busy}
              onClick={() =>
                void run(async () => {
                  const result = await updateUser({
                    username: user.username,
                    generate_password: true,
                  });
                  ensureOk(result, "Could not generate a password.");
                  if (!result.generated_password) {
                    throw new Error("The server did not return a generated password.");
                  }
                  setGenerated(result.generated_password);
                  setCopied(false);
                  clearPasswordFields();
                }, "Password generated — copy it now.")
              }
            >
              Generate
            </Button>
          )}
        </div>

        {generated && (
          <ScopeNote tone="warn">
            <div style={{ display: "grid", gap: "var(--space-2)" }}>
              <div>Copy this password now. It is shown only once.</div>
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: "var(--space-2)",
                  alignItems: "center",
                }}
              >
                <Mono>{generated}</Mono>
                <Button disabled={busy} onClick={() => void copyGenerated(generated)}>
                  {copied ? "Copied" : "Copy"}
                </Button>
              </div>
            </div>
          </ScopeNote>
        )}
      </div>
    </Drawer>
  );
}

function CreateUserCard({
  roles,
  busy,
  onCreate,
}: {
  roles: RoleInfo[];
  busy: boolean;
  onCreate: (input: {
    username: string;
    password: string;
    role: Role;
    display_name: string;
    email: string;
  }) => Promise<boolean>;
}) {
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("user");


  return (
    <Card
      title="Add an account"
      description="The new user must change this password at first sign-in."
    >
      <form
        onSubmit={(event) => {
          // Reached only when the browser is satisfied. `required` rejects an
          // empty field but not "   ", and the handler sends `username.trim()`
          // -- so the pattern on the input below is what makes "not just
          // whitespace" a rule the browser can report, rather than one the
          // server discovers.
          event.preventDefault();
          if (busy) return;
          void onCreate({
            username: username.trim(),
            password,
            role,
            display_name: displayName.trim(),
            email: email.trim(),
          }).then((created) => {
            // Only on success. Clearing regardless threw away the password on
            // every rejected create, which is the worst moment to ask someone
            // to retype one.
            if (!created) return;
            setUsername("");
            setDisplayName("");
            setEmail("");
            setPassword("");
            setRole("user");
          });
        }}
        style={{ display: "grid", gap: "var(--space-3)", padding: "var(--space-3) 0" }}
      >
        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
          <TextInput
            placeholder="Username"
            value={username}
            required
            // At least one non-space. `required` alone accepts "   ", which
            // trims to the empty username the server then rejects.
            pattern=".*\S.*"
            title="A username cannot be only spaces."
            onChange={(e) => setUsername(e.target.value)}
            aria-label="Username"
          />
          <TextInput
            placeholder="Display name"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            aria-label="Display name"
          />
          <TextInput
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-label="Email"
          />
        </div>
        <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap", alignItems: "center" }}>
          <TextInput
            type="password"
            placeholder={`Initial password, at least ${MIN_PASSWORD_LENGTH} characters`}
            value={password}
            required
            minLength={MIN_PASSWORD_LENGTH}
            onChange={(e) => setPassword(e.target.value)}
            aria-label="Initial password"
          />
          <Select value={role} onChange={(e) => setRole(e.target.value as Role)} aria-label="Role">
            {roles.map((r) => (
              <option key={r.id} value={r.id}>
                {r.title}
              </option>
            ))}
          </Select>
          {/* Disabled only while the request is in flight. It used to be
              disabled whenever the fields were incomplete, which meant an empty
              username produced a button that did nothing at all: a disabled
              control cannot explain itself, and the browser never runs
              constraint validation on a submit that cannot fire. Now the form
              submits, the browser names the offending field, and
              `:user-invalid` in primitives.module.css marks it. */}
          <Button type="submit" variant="primary" disabled={busy}>
            Create account
          </Button>
        </div>
        <p style={{ fontSize: "var(--text-sm)", color: "var(--ink-3)", margin: 0 }}>
          Minimum eight characters. The role decides which parts of this interface
          the account can reach and which endpoints accept its requests.
        </p>
      </form>
    </Card>
  );
}
