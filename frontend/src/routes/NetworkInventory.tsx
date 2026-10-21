/**
 * The device inventory and the credential store, edited from the console.
 *
 * The shape of this panel follows the rule the executor enforces: **an inventory
 * names a credential, it never contains one.** A group has a credential *name*
 * chosen from what is stored; the secret itself is entered in the other half of
 * this panel and goes straight to the executor. There is deliberately no field
 * anywhere here that puts a password into a group's variables, because the
 * executor would refuse it and because a form that offered it would be teaching
 * the wrong thing.
 *
 * Nothing displays a stored secret, because nothing can: the executor has no
 * endpoint that returns one. A fingerprint stands in, which answers the only
 * question anyone legitimately has — is the value I am holding the value that is
 * loaded — without being the value.
 */

import { useCallback, useRef, useState } from "react";

/**
 * What a group's platform means to Ansible.
 *
 * A group created here carried no `ansible_network_os` and no
 * `ansible_connection`, so every job against it ran over plain `ssh` and the
 * first task answered `Connection type ssh is not valid for this module`. The
 * executor's renderer accepted both variables all along and the import-JSON path
 * carried them; there was simply no field, so the only way to set them was to
 * export the inventory, edit it by hand and import it back. That worked and was
 * undiscoverable.
 *
 * `network_cli` for both: these are CLI-driven platforms, which is what the
 * catalogue's playbooks are written against. A platform that needs a different
 * connection would be a new entry here and a new dispatch branch in the
 * playbooks, together.
 *
 * The keys are the catalogue's own platform names.
 * `plugins/_ea_ansible/tests/test_net_inventory_platforms.py` asserts this list
 * and the catalogue agree, so adding a platform to one and forgetting the other
 * fails rather than shipping a form that cannot express what the catalogue runs.
 */
const PLATFORMS: Record<string, { label: string; network_os: string; connection: string }> = {
  ios: {
    label: "Cisco IOS",
    network_os: "cisco.ios.ios",
    connection: "ansible.netcommon.network_cli",
  },
  nxos: {
    label: "Cisco NX-OS",
    network_os: "cisco.nxos.nxos",
    connection: "ansible.netcommon.network_cli",
  },
  eos: {
    label: "Arista EOS",
    network_os: "arista.eos.eos",
    connection: "ansible.netcommon.network_cli",
  },
  junos: {
    label: "Juniper Junos",
    network_os: "junipernetworks.junos.junos",
    connection: "ansible.netcommon.network_cli",
  },
  sonic: {
    label: "SONiC",
    network_os: "dellemc.enterprise_sonic.sonic",
    connection: "ansible.netcommon.network_cli",
  },
  sonic_community: {
    label: "SONiC (community)",
    network_os: "sonic_community",
    connection: "paramiko_ssh",
  },
  // FortiGate and Palo Alto are read through their HTTPS API, which the executor calls itself with
  // an api_token credential; these two record `local`, as the Inventory sync does: no Ansible connection to the device.
  fortios: {
    label: "Fortinet FortiGate",
    network_os: "fortinet.fortios.fortios",
    connection: "local",
  },
  panos: {
    label: "Palo Alto PAN-OS",
    network_os: "paloaltonetworks.panos.panos",
    connection: "local",
  },
};

/** The platform key a group's vars currently describe, or "" for none. */
function platformOf(group: InventoryGroup): string {
  const current = String(group.vars?.["ansible_network_os"] ?? "");
  const found = Object.entries(PLATFORMS).find(([, spec]) => spec.network_os === current);
  return found ? found[0] : "";
}

/**
 * Set or clear both variables together.
 *
 * Together because they are not independent: a network_os with no network_cli
 * connection is the failure this field exists to remove, and a connection with
 * no network_os leaves the playbooks unable to dispatch. Other vars on the group
 * are preserved -- the import path can carry things this form does not offer,
 * and choosing a platform must not quietly drop them.
 */
function withPlatform(group: InventoryGroup, key: string): InventoryGroup {
  const vars = { ...group.vars };
  delete vars["ansible_network_os"];
  delete vars["ansible_connection"];
  const spec = PLATFORMS[key];
  if (spec) {
    vars["ansible_network_os"] = spec.network_os;
    vars["ansible_connection"] = spec.connection;
  }
  return { ...group, vars };
}

import {
  deleteCredential,
  getInventory,
  saveCredential,
  saveInventory,
  type CredentialSummary,
  type InventoryDocument,
  type InventoryGroup,
} from "@/api/networkConsole";
import {
  Button,
  Card,
  CenteredMessage,
  Mono,
  ScopeNote,
  SearchInput,
  Select,
  Table,
  Tag,
  TextInput,
  useConfirm,
  useToast,
} from "@/components";
import { useAsync } from "@/lib/useAsync";
import { ExecutorRefused } from "./ExecutorState";
import styles from "./network.module.css";

const KINDS = ["password", "ssh_key", "api_token", "enable"] as const;

const EMPTY: InventoryDocument = { groups: {}, hosts: {} };

export function InventoryPanel() {
  const load = useCallback(() => getInventory(), []);
  const { state, reload } = useAsync(load, [load]);
  const toast = useToast();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const importRef = useRef<HTMLInputElement | null>(null);

  if (state.status === "loading") return <CenteredMessage title="Loading the inventory…" />;
  if (state.status === "error") {
    return (
      <CenteredMessage
        title="Could not reach the executor"
        detail={state.error.message}
        action={<Button variant="primary" onClick={reload}>Retry</Button>}
      />
    );
  }
  if (!state.data.ok) {
    return (
      <ExecutorRefused error={state.data.error} onRetry={reload} />
    );
  }

  const inventory = state.data.inventory ?? EMPTY;
  const credentials = state.data.credentials ?? [];
  const unresolved = state.data.unresolved_credentials ?? [];

  const run = async (work: () => Promise<unknown>, success?: string) => {
    setBusy(true);
    setError("");
    try {
      await work();
      reload();
      if (success) toast.success(success);
    } catch (problem) {
      const message = problem instanceof Error ? problem.message : String(problem);
      setError(message);
      toast.error(message);
    } finally {
      setBusy(false);
    }
  };

  // The inventory is portable JSON, so let an engineer keep a copy or move one
  // between instances rather than retyping every device.
  function exportInventory() {
    const current = state.status === "ready" ? state.data.inventory ?? EMPTY : EMPTY;
    const blob = new Blob([JSON.stringify(current, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = "network-inventory.json";
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function importInventory(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const parsed = JSON.parse(String(reader.result)) as InventoryDocument;
        if (!parsed || typeof parsed !== "object" || !("groups" in parsed) || !("hosts" in parsed)) {
          throw new Error("That file is not an inventory document (needs groups and hosts).");
        }
        void run(() => saveInventory(parsed), "Inventory imported.");
      } catch (problem) {
        toast.error(problem instanceof Error ? problem.message : String(problem));
      }
    };
    reader.readAsText(file);
  }

  return (
    <>
      <ScopeNote>
        <div>
          This inventory lives on the executor and nowhere else — not in this
          application, and not in the runner. Groups are what the agent selects
          with, so name them for how people talk about the estate rather than for
          the vendor.
        </div>
      </ScopeNote>

      {error && <ScopeNote tone="crit"><div>{error}</div></ScopeNote>}

      <div style={{ display: "flex", gap: "var(--space-2)", flexWrap: "wrap" }}>
        <Button disabled={busy} onClick={exportInventory}>
          Export inventory
        </Button>
        <Button disabled={busy} onClick={() => importRef.current?.click()}>
          Import inventory
        </Button>
        <input
          ref={importRef}
          type="file"
          accept="application/json,.json"
          className="visually-hidden"
          aria-label="Import inventory JSON"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) importInventory(file);
            event.target.value = "";
          }}
        />
      </div>

      {unresolved.length > 0 && (
        <ScopeNote tone="warn">
          <div>
            {unresolved.length === 1 ? "A group names a credential" : "Groups name credentials"}{" "}
            that is not stored here yet: {unresolved.map((name) => <Mono key={name}>{name} </Mono>)}
            . Jobs against {unresolved.length === 1 ? "that group" : "those groups"} will refuse
            until it is entered below.
          </div>
        </ScopeNote>
      )}

      <HostsCard
        inventory={inventory}
        busy={busy}
        onSave={(next) => void run(() => saveInventory(next), "Inventory saved.")}
      />

      <GroupsCard
        inventory={inventory}
        credentials={credentials}
        busy={busy}
        onSave={(next) => void run(() => saveInventory(next), "Inventory saved.")}
      />

      <CredentialsCard
        credentials={credentials}
        busy={busy}
        onSave={(input) => void run(() => saveCredential(input), "Credential stored.")}
        onDelete={(name) => void run(() => deleteCredential(name), "Credential deleted.")}
      />
    </>
  );
}

/* --------------------------------- hosts --------------------------------- */

function HostsCard({
  inventory,
  busy,
  onSave,
}: {
  inventory: InventoryDocument;
  busy: boolean;
  onSave: (next: InventoryDocument) => void;
}) {
  const { confirm } = useConfirm();
  const [name, setName] = useState("");
  const [address, setAddress] = useState("");
  const [editing, setEditing] = useState<{ host: string; address: string } | null>(null);

  const add = () => {
    if (!name.trim() || !address.trim()) return;
    onSave({
      ...inventory,
      hosts: { ...inventory.hosts, [name.trim()]: { ansible_host: address.trim(), vars: {} } },
    });
    setName("");
    setAddress("");
  };

  const saveAddress = (host: string, next: string) => {
    const entry = inventory.hosts[host];
    if (!entry || !next.trim()) return;
    onSave({
      ...inventory,
      hosts: { ...inventory.hosts, [host]: { ...entry, ansible_host: next.trim() } },
    });
    setEditing(null);
  };

  const remove = async (host: string) => {
    const ok = await confirm({
      title: `Remove ${host}?`,
      body: "It is taken out of the inventory and out of any group that names it.",
      danger: true,
      confirmLabel: "Remove",
    });
    if (!ok) return;
    const hosts = { ...inventory.hosts };
    delete hosts[host];
    // A group left naming a removed host would be refused by the executor, so
    // the membership goes with it rather than producing an inventory that
    // cannot be saved.
    const groups = Object.fromEntries(
      Object.entries(inventory.groups).map(([groupName, group]) => [
        groupName,
        { ...group, hosts: group.hosts.filter((member) => member !== host) },
      ]),
    );
    onSave({ groups, hosts });
  };

  const hosts = Object.entries(inventory.hosts);

  return (
    <Card title="Devices" description={`${hosts.length} in the inventory`}>
      {hosts.length ? (
        <Table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Address</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {hosts.map(([host, entry]) => (
              <tr key={host}>
                <td><Mono>{host}</Mono></td>
                <td>
                  {editing?.host === host ? (
                    <TextInput
                      value={editing.address}
                      aria-label={`Address for ${host}`}
                      onChange={(event) => setEditing({ host, address: event.target.value })}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") saveAddress(host, editing.address);
                        if (event.key === "Escape") setEditing(null);
                      }}
                    />
                  ) : (
                    <Mono>{entry.ansible_host}</Mono>
                  )}
                </td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {editing?.host === host ? (
                    <>
                      <Button
                        variant="primary"
                        disabled={busy}
                        onClick={() => saveAddress(host, editing.address)}
                      >
                        Save
                      </Button>{" "}
                      <Button disabled={busy} onClick={() => setEditing(null)}>
                        Cancel
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        disabled={busy}
                        onClick={() => setEditing({ host, address: entry.ansible_host })}
                      >
                        Edit
                      </Button>{" "}
                      <Button disabled={busy} onClick={() => void remove(host)}>
                        Remove
                      </Button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <p>No devices yet.</p>
      )}

      <div className={styles.hostRow}>
        <TextInput
          value={name}
          placeholder="core-sw-01"
          aria-label="Device name"
          onChange={(event) => setName(event.target.value)}
        />
        <TextInput
          value={address}
          placeholder="10.0.0.1"
          aria-label="Address"
          onChange={(event) => setAddress(event.target.value)}
        />
        <Button variant="primary" disabled={busy} onClick={add}>Add device</Button>
      </div>
    </Card>
  );
}

/* --------------------------------- groups -------------------------------- */

function GroupsCard({
  inventory,
  credentials,
  busy,
  onSave,
}: {
  inventory: InventoryDocument;
  credentials: CredentialSummary[];
  busy: boolean;
  onSave: (next: InventoryDocument) => void;
}) {
  const { confirm } = useConfirm();
  const [name, setName] = useState("");
  const [hostQuery, setHostQuery] = useState("");

  const add = () => {
    if (!name.trim()) return;
    onSave({
      ...inventory,
      groups: {
        ...inventory.groups,
        [name.trim()]: { hosts: [], vars: {}, credential: "" },
      },
    });
    setName("");
  };

  // Takes the whole group rather than a patch. Spreading a Partial over a type
  // whose fields are required widens them back to optional under
  // exactOptionalPropertyTypes, and the compiler is right to say so: a patch
  // that dropped `hosts` would silently empty a group.
  const update = (groupName: string, next: InventoryGroup) => {
    onSave({ ...inventory, groups: { ...inventory.groups, [groupName]: next } });
  };

  const remove = async (groupName: string) => {
    const ok = await confirm({
      title: `Remove group ${groupName}?`,
      body: "The devices stay in the inventory; only the group is removed.",
      danger: true,
      confirmLabel: "Remove group",
    });
    if (!ok) return;
    const groups = { ...inventory.groups };
    delete groups[groupName];
    onSave({ ...inventory, groups });
  };

  const groups = Object.entries(inventory.groups);
  const hostNames = Object.keys(inventory.hosts);
  const query = hostQuery.trim().toLowerCase();
  const visibleHosts = query
    ? hostNames.filter((host) => host.toLowerCase().includes(query))
    : hostNames;

  return (
    <Card title="Groups" description="What the agent selects with">
      {groups.length ? (
        groups.map(([groupName, group]) => (
          <Card key={groupName} title={groupName}>
            <div className={styles.hostRow}>
              <label>
                Credential
                <Select
                  value={group.credential}
                  disabled={busy}
                  onChange={(event) => update(groupName, { ...group, credential: event.target.value })}
                >
                  <option value="">none</option>
                  {credentials.map((credential) => (
                    <option key={credential.name} value={credential.name}>
                      {credential.name} ({credential.kind})
                    </option>
                  ))}
                </Select>
              </label>
              <label>
                Platform
                <Select
                  value={platformOf(group)}
                  disabled={busy}
                  onChange={(event) => update(groupName, withPlatform(group, event.target.value))}
                >
                  <option value="">not set</option>
                  {Object.entries(PLATFORMS).map(([key, spec]) => (
                    <option key={key} value={key}>{spec.label}</option>
                  ))}
                </Select>
              </label>
              <Button disabled={busy} onClick={() => void remove(groupName)}>Remove group</Button>
            </div>
            {!platformOf(group) && (
              <p className={styles.platformHint}>
                Without a platform this group runs over plain SSH, and a job
                against it fails on the first task. Set the one these devices
                speak.
              </p>
            )}

            {hostNames.length ? (
              <div>
                {hostNames.length > 8 && (
                  <div style={{ maxWidth: 260, marginBottom: "var(--space-2)" }}>
                    <SearchInput
                      value={hostQuery}
                      onChange={setHostQuery}
                      placeholder="Find a device…"
                      label="Find a device"
                    />
                  </div>
                )}
                <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                  {visibleHosts.map((host) => {
                    const member = group.hosts.includes(host);
                    return (
                      <Button
                        key={host}
                        variant={member ? "primary" : "default"}
                        disabled={busy}
                        onClick={() =>
                          update(groupName, {
                            ...group,
                            hosts: member
                              ? group.hosts.filter((item) => item !== host)
                              : [...group.hosts, host],
                          })
                        }
                      >
                        {host}
                      </Button>
                    );
                  })}
                  {visibleHosts.length === 0 && (
                    <span style={{ color: "var(--ink-3)", fontSize: "var(--text-sm)" }}>
                      No devices match “{hostQuery}”.
                    </span>
                  )}
                </div>
              </div>
            ) : (
              <p>Add a device before putting one in a group.</p>
            )}
          </Card>
        ))
      ) : (
        <p>No groups yet.</p>
      )}

      <div className={styles.hostRow}>
        <TextInput
          value={name}
          placeholder="core-switches"
          aria-label="Group name"
          onChange={(event) => setName(event.target.value)}
        />
        <span />
        <Button variant="primary" disabled={busy} onClick={add}>Add group</Button>
      </div>
    </Card>
  );
}

/* ------------------------------ credentials ------------------------------ */

function CredentialsCard({
  credentials,
  busy,
  onSave,
  onDelete,
}: {
  credentials: CredentialSummary[];
  busy: boolean;
  onSave: (input: { name: string; kind: string; secret: string; username?: string }) => void;
  onDelete: (name: string) => void;
}) {
  const { confirm } = useConfirm();
  const toast = useToast();
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [secret, setSecret] = useState("");
  const [kind, setKind] = useState<string>("password");

  const submit = () => {
    // Said, not silently refused. The rule lived here and in the button's
    // disabled expression, and was written to the user in neither -- so a blank
    // credential name produced a dead "Store" with no marked field and no
    // required indicator anywhere on the card.
    const missing = !name.trim()
      ? "A credential needs a name."
      : !secret
        ? "A credential needs a secret."
        : null;
    if (missing) {
      toast.error(missing);
      return;
    }
    onSave({ name: name.trim(), kind, secret, username: username.trim() });
    setName("");
    setUsername("");
    setSecret("");
  };

  return (
    <Card title="Credentials" description="Stored on the executor, encrypted, and never readable back">
      <ScopeNote tone="warn">
        <div>
          <strong>There is no way to read one of these back.</strong> Not here,
          not from the executor, not for debugging. If you have lost a device
          password, rotate it rather than trying to recover it. The fingerprint
          below tells you whether the value you are holding is the one that is
          loaded.
        </div>
      </ScopeNote>

      {credentials.length ? (
        <Table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Kind</th>
              <th>Username</th>
              <th>Fingerprint</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {credentials.map((credential) => (
              <tr key={credential.name}>
                <td><Mono>{credential.name}</Mono></td>
                <td><Tag>{credential.kind}</Tag></td>
                <td>{credential.username || "—"}</td>
                <td className={styles.fingerprint}><Mono>{credential.fingerprint}</Mono></td>
                <td>
                  <Button
                    variant="danger"
                    disabled={busy}
                    onClick={async () => {
                      const ok = await confirm({
                        title: `Delete credential ${credential.name}?`,
                        body: "Any group naming it will refuse jobs until another is entered. This cannot be undone.",
                        danger: true,
                        confirmLabel: "Delete",
                      });
                      if (ok) onDelete(credential.name);
                    }}
                  >
                    Delete
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </Table>
      ) : (
        <p>Nothing is stored yet.</p>
      )}

      <div className={styles.hostRow}>
        <TextInput
          value={name}
          placeholder="core-switch-login"
          aria-label="Credential name"
          onChange={(event) => setName(event.target.value)}
        />
        <Select value={kind} onChange={(event) => setKind(event.target.value)} aria-label="Kind">
          {KINDS.map((value) => (
            <option key={value} value={value}>{value}</option>
          ))}
        </Select>
        <span />
      </div>
      {kind === "api_token" && (
        <ScopeNote>
          <div>
            For a FortiGate or Palo Alto firewall, create the token on the device with a{" "}
            <strong>read-only</strong> role, and limit where it can be used from. FortiGate:{" "}
            System → Administrators → Create New → REST API Admin, a read-only admin profile, and trusted hosts
            set to this executor's address (
            <a href="https://docs.fortinet.com/document/fortigate/7.4.0/administration-guide/940602/rest-api" target="_blank" rel="noreferrer">
              Fortinet guide
            </a>
            ). Palo Alto: an administrator whose role is Superreader or a custom role that allows the XML API read-only,
            then generate its API key (
            <a href="https://docs.paloaltonetworks.com/pan-os/10-0/pan-os-panorama-api/get-started-with-the-pan-os-xml-api/get-your-api-key" target="_blank" rel="noreferrer">
              Palo Alto guide
            </a>
            ). The username can stay empty.
          </div>
        </ScopeNote>
      )}
      <div className={styles.hostRow}>
        <TextInput
          value={username}
          placeholder="netops"
          aria-label="Username"
          onChange={(event) => setUsername(event.target.value)}
        />
        <TextInput
          value={secret}
          type="password"
          placeholder="secret"
          aria-label="Secret"
          onChange={(event) => setSecret(event.target.value)}
        />
        <Button variant="primary" disabled={busy} onClick={submit}>
          Store
        </Button>
      </div>
    </Card>
  );
}
