# The appliance on a physical server, with one port on a trunk

For an appliance installed on a physical server that has **one** network cable to a switch. The switch port is a
trunk that carries the VLANs; the appliance makes one interface for each VLAN on that one port.

> **Pictures are still to be added.** The console screens below are the tool's own text, copied from a run of it
> (a test compares them with the tool on every change). This has not been installed on a physical server yet; the switch commands are standard Cisco IOS and were not typed on a switch for this guide. Screenshots from a real run will replace this note.

## The example

| Network | What is in it | Gateway | The appliance's address in this example |
| --- | --- | --- | --- |
| VLAN 300 | The internet (the model, the licence check) and the users who open the web interface | 10.0.30.1 | 10.0.30.5/24 |
| VLAN 100 | Switches, routers, firewalls. No gateway: only a machine with an address in this VLAN reaches them | none | 10.0.100.5/24 |
| VLAN 200 | Devices, and more device subnets behind its gateway (here 10.20.0.0/16) | 10.0.200.1 | 10.0.200.5/24 |

Every address in this guide is an example. Use your own.

## 1. On the switch: a trunk port that allows the VLANs

Cisco IOS, for the port the server is cabled to. The port name and the VLAN numbers are examples.

```
interface GigabitEthernet1/0/10
 description Rezztor EAgent appliance
 switchport mode trunk
 switchport trunk allowed vlan 100,200,300
 switchport nonegotiate
 spanning-tree portfast trunk
```

- On some older switches the line `switchport trunk encapsulation dot1q` must come before `switchport mode trunk`.
- On newer software the last line is `spanning-tree portfast edge trunk`.
- First boot tries the port untagged and waits up to 30 seconds for DHCP. On a trunk that carries only tagged
  VLANs that wait fails; then the tool asks for the VLAN ID and tags the frames. Leave the native VLAN as it is,
  or set it to a VLAN you do not use.
- Allow only the VLANs the appliance needs. A VLAN that is not in the list does not reach it.
- To check: `show interfaces GigabitEthernet1/0/10 trunk` must list 100, 200 and 300 in the line "Vlans allowed and
  active in management domain". A VLAN that is missing there does not exist on this switch yet (`vlan 100`).

## 2. Install, and set up the first interface

Start the server from the ISO and install as in [the install guide](install-from-iso-vmware.md), sections 3 and 4.
At the network step the tool sets up **one** interface: the one you will use to reach the appliance. More
interfaces are added later from the console menu (`sudo rezztor-setup network`).

The first try uses the port untagged. Press Enter at the DHCP question. On a trunk there is usually no DHCP
lease; the tool waits up to 30 seconds, then says so. That wait is expected.

<!-- screen: case-b -->
```text
The network: the interface you reach this machine on. More interfaces are added later, from the console menu.
Does the network give this machine its address by itself (DHCP)? [y]:
no address came from DHCP on eno1 within 30 seconds. Check the cable and the DHCP server, or type a fixed address.
Nothing was changed: the settings from before are in force.
```

## 3. First, the network you reach the appliance on

The questions come again. Type the VLAN ID of the network you reach the appliance on (here 300), then a fixed
address, the gateway and DNS.

<!-- screen: case-b -->
```text
The questions again.
If this port is a trunk: the VLAN ID to use on it, 1 to 4094 (none: the port is untagged): 300
Does the network give this machine its address by itself (DHCP)? [n]:
Address and prefix, for example 192.168.10.20/24: 10.0.30.5/24
Gateway, for example 192.168.10.1 (none if there is no way out of this network): 10.0.30.1
DNS servers, separated by spaces (at most three) [10.0.30.1]:
Network port:   vlan300, up, gateway answers
Address:        10.0.30.5/24
Gateway:        10.0.30.1
Web interface:  https://10.0.30.5
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
```

Every change ends with the same question: **Keep these settings?** Type `keep` and press Enter within 120 seconds.
If you type `back`, type nothing, or lose the console, the appliance goes back by itself to the settings from before.
The timer does not belong to your session, so a closed window does not stop it.

## 4. Then the device networks, from the menu

Sign in as `rezadmin` and open the network menu:

```bash
sudo rezztor-setup network
```

Choose **2) Add an interface**. The tool names each tagged interface itself (`VLAN 100`, `VLAN 200`). Answer **n**
when asked whether the web interface and SSH may answer on the new interface.

VLAN 100 has no gateway:

<!-- screen: case-b-menu -->
```text
Choice [0]: 2
Not in this build: bonds, a second internet uplink, the same subnet in two VLANs, jump hosts,
inbound traps, syslog or TFTP, jumbo frames, and IPv6 on device networks.

This machine has one interface so far, now called VLAN 300. With a second one, the product reaches
devices only in the networks named here: each interface's own network, and the routes on it.
A device network behind the gateway of VLAN 300 is named under Routes, with that gateway.
Network ports:
  1) eno1         00:50:56:aa:bb:01  link up   used by VLAN 300
Which port? [1]:
VLAN ID on this port, 1 to 4094 (none: the port is untagged): 100
May the web interface and SSH answer on this interface? [n]:
Address and prefix, for example 10.20.30.10/24: 10.0.100.5/24
  A gateway here is the next hop of the routes you name on this interface. It never becomes a second default route.
Gateway on this network (none: no gateway):
Interface  Used for                Port     Address        Gateway    Reaches          State
VLAN 300   Internet+Users+Devices  vlan300  10.0.30.5/24   10.0.30.1  everything else  up, gateway answers
VLAN 100   Devices                 vlan100  10.0.100.5/24  none       10.0.100.0/24    up

Web interface:  https://10.0.30.5   (answers on VLAN 300 only)
SSH:            VLAN 300 only, while SSH is switched on
On your devices, allow management from:  10.0.30.5 (VLAN 300),  10.0.100.5 (VLAN 100)
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
```

VLAN 200 has a gateway and more device subnets behind it. Type the gateway, then the networks reached through it
(here `10.20.0.0/16`). That gateway is only the next hop of those routes; it never becomes a second default route.

<!-- screen: case-b-menu -->
```text
Choice [0]: 2
Network ports:
  1) eno1         00:50:56:aa:bb:01  link up   used by VLAN 300, VLAN 100
Which port? [1]:
VLAN ID on this port, 1 to 4094 (none: the port is untagged): 200
May the web interface and SSH answer on this interface? [n]:
Address and prefix, for example 10.20.30.10/24: 10.0.200.5/24
  A gateway here is the next hop of the routes you name on this interface. It never becomes a second default route.
Gateway on this network (none: no gateway): 10.0.200.1
Networks reached through that gateway, separated by spaces, for example 172.16.0.0/24 10.100.100.0/24
(none: add them later under Routes): 10.20.0.0/16
Interface  Used for                Port     Address        Gateway     Reaches                      State
VLAN 300   Internet+Users+Devices  vlan300  10.0.30.5/24   10.0.30.1   everything else              up, gateway answers
VLAN 100   Devices                 vlan100  10.0.100.5/24  none        10.0.100.0/24                up
VLAN 200   Devices                 vlan200  10.0.200.5/24  10.0.200.1  10.0.200.0/24, 10.20.0.0/16  up, gateway answers

Web interface:  https://10.0.30.5   (answers on VLAN 300 only)
SSH:            VLAN 300 only, while SSH is switched on
On your devices, allow management from:  10.0.30.5 (VLAN 300),  10.0.100.5 (VLAN 100),  10.0.200.5 (VLAN 200)
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
```

Show the interfaces when you are done. The table columns are Interface, Used for, Port, Address, Gateway, Reaches
and State.

<!-- screen: case-b-menu -->
```text
Choice [0]: 1
Interface  Used for                Port     Address        Gateway     Reaches                      State
VLAN 300   Internet+Users+Devices  vlan300  10.0.30.5/24   10.0.30.1   everything else              up
VLAN 100   Devices                 vlan100  10.0.100.5/24  none        10.0.100.0/24                up
VLAN 200   Devices                 vlan200  10.0.200.5/24  10.0.200.1  10.0.200.0/24, 10.20.0.0/16  up

Web interface:  https://10.0.30.5   (answers on VLAN 300 only)
SSH:            VLAN 300 only, while SSH is switched on
On your devices, allow management from:  10.0.30.5 (VLAN 300),  10.0.100.5 (VLAN 100),  10.0.200.5 (VLAN 200)
```

Once there are several interfaces, the menu also offers **8) Services inside device networks that the product may
use** for a monitoring or log server that the product itself must reach. The product reaches devices only in the
networks named: each interface's own network and its routes.

## 5. Check

- Every interface says **up** in the table. "gateway does not answer" on one VLAN usually means the VLAN is
  missing from `switchport trunk allowed vlan`.
- From a switch in VLAN 100: `ping 10.0.100.5` is answered. Nothing else on the appliance answers in a device network.
- The line **On your devices, allow management from** gives the addresses to allow on your devices:
  [What to allow on your devices](appliance-what-to-allow-on-devices.md).

## If something goes wrong

| What you see | What to do |
| --- | --- |
| `the address 10.0.100.5 is already used by another machine (00:11:22:33:44:55)` | Another machine has this address. Choose a free one. Nothing was changed. |
| `Warning: ... the gateway 10.0.30.1 does not answer.` | A warning, not a refusal: some gateways do not answer. Check the address, and that the VLAN is allowed on the switch port. |
| `no address came from DHCP on eno1 within 30 seconds` | Expected on a trunk that carries only tagged VLANs. Type the VLAN ID when the questions come again. |
| `... overlaps ..., which the containers on this machine use.` | One of your networks uses the addresses the product's own containers use. Answer `y` when the tool offers another block. |
| `... overlaps the subnet ... of ...` | Two of your networks would lead to the same addresses. One address must lead to one place. |
| `The firewall refused these networks` | The appliance went back by itself. Write down the text and send it to support. |

## Not in this build

Bonds (two ports as one), a second internet uplink, the same subnet in two VLANs, jump hosts, traps, syslog or TFTP
sent **to** the appliance, jumbo frames, and IPv6 on device networks. The tool says so too, and refuses what it
cannot do with a sentence that names the problem.
