/**
 * The networks the appliance is on, read-only.
 *
 * The list is the appliance console's file, read through the executor. Nothing here changes a
 * network: that is the appliance console's menu Network, and the screen says so. With no list
 * (a single flat network, or an appliance that has not written one) it says nothing is known
 * rather than showing an empty table.
 */

import { getNetworks } from "@/api/networkConsole";
import { Card, CenteredMessage, Mono, ScopeNote, Table, Tag } from "@/components";
import { useAsync } from "@/lib/useAsync";

export function NetworksPanel() {
  const { state } = useAsync(() => getNetworks(), []);

  if (state.status === "loading") return <CenteredMessage title="Reading the networks…" />;
  if (state.status === "error") {
    return <CenteredMessage title="Could not read the networks" detail={state.error.message} />;
  }
  const list = state.data;

  return (
    <Card title="Networks" description="The networks this appliance is on">
      {!list.known ? (
        <CenteredMessage
          title="One network"
          detail="This appliance is on one network, and devices are reached through it. Separate networks for devices are added on the appliance console, menu Network."
        />
      ) : (
        <>
          <Table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Roles</th>
                <th>Interface</th>
                <th>Address</th>
                <th>Gateway</th>
                <th>Reaches</th>
                <th>State</th>
              </tr>
            </thead>
            <tbody>
              {list.networks.map((n) => (
                <tr key={n.name}>
                  <td>{n.name}</td>
                  <td>{n.roles.join(", ")}</td>
                  <td><Mono>{n.interface || "—"}</Mono></td>
                  <td><Mono>{n.address || "—"}</Mono></td>
                  <td>{n.gateway ? <Mono>{n.gateway}</Mono> : "—"}</td>
                  <td>
                    {[n.subnet, ...n.routed_subnets].filter(Boolean).map((s) => (
                      <div key={s}><Mono>{s}</Mono></div>
                    ))}
                  </td>
                  <td><Tag tone={n.state === "up" ? "ok" : "crit"}>{n.state}</Tag></td>
                </tr>
              ))}
            </tbody>
          </Table>
          {list.hint && <p>{list.hint}</p>}
        </>
      )}
      <ScopeNote>
        <div>
          This view only reads. Networks are added, changed and removed on the appliance console,
          menu Network.
        </div>
      </ScopeNote>
    </Card>
  );
}
