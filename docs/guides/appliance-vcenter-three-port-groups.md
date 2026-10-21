# The appliance on vCenter, with one network card per port group

For an appliance (installed from the ISO) that sits in several networks on VMware: one for the internet and the
users, and one or more for the devices it manages. This is the usual way on vCenter: **one virtual network card for
each port group**. VMware writes the VLAN tag, so the appliance sees three ordinary network ports.

If your VMware team gives you one port group that carries all VLANs instead, use
[the trunk port group guide](appliance-vcenter-trunk-port-group.md). For a physical server, use
[the physical server guide](appliance-physical-server-trunk.md).

> **Pictures are still to be added.** The console screens below are the tool's own text, copied from a run of it
> (a test compares them with the tool on every change). The vCenter steps are written from VMware's documentation and have not been followed on a real vCenter yet. Screenshots from a real run will replace this note.

## The example

| Network | What is in it | Gateway | The appliance's address in this example |
| --- | --- | --- | --- |
| VLAN 300 | The internet (the model, the licence check) and the users who open the web interface | 10.0.30.1 | 10.0.30.5/24 |
| VLAN 100 | Switches, routers, firewalls. No gateway: only a machine with an address in this VLAN reaches them | none | 10.0.100.5/24 |
| VLAN 200 | Devices, and more device subnets behind its gateway (here 10.20.0.0/16) | 10.0.200.1 | 10.0.200.5/24 |

Every address in this guide is an example. Use your own.

**On one network only?** Then you do not need this guide. First boot sets up that one interface. Press Enter if the
network gives the machine its address:

<!-- screen: flat -->
```text
The network: the interface you reach this machine on. More interfaces are added later, from the console menu.
Does the network give this machine its address by itself (DHCP)? [y]:
Network port:   ens192, up, gateway answers
Address:        192.168.10.57/24  (from DHCP)
Gateway:        192.168.10.1
Web interface:  https://192.168.10.57
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
```

## 1. In vCenter: three port groups, three network cards

1. Make sure a port group exists for each network, each with its **VLAN ID** (300, 100, 200). Use the port groups
   you already have for these VLANs.
2. Create the virtual machine as in [Install from the test ISO on VMware](install-from-iso-vmware.md), but give it
   **three network adapters** (type VMXNET 3), one connected to each port group. Tick *Connect at power on* for each.
3. Write down the **MAC address** of each adapter and its port group (Edit Settings, open *Network adapter 1*, *2*,
   *3*). The appliance shows the same MAC addresses, so you can tell the ports apart.

   | Adapter | Port group | MAC address in this example |
   | --- | --- | --- |
   | Network adapter 1 | VLAN 300 | 00:50:56:aa:bb:01 |
   | Network adapter 2 | VLAN 100 | 00:50:56:aa:bb:02 |
   | Network adapter 3 | VLAN 200 | 00:50:56:aa:bb:03 |

A virtual machine can have at most 10 network adapters. With more device networks than that, use a trunk port group.

## 2. Install, and set up the first interface

Install from the ISO as in the install guide. After the keyboard and the console password, first boot sets up
**one** interface: the network you will use to reach the machine. More interfaces are added later from the console
menu. Choose the port by its MAC address. The port is **not** tagged: VMware does that. Answer `n` at DHCP and type
the address, the gateway and DNS:

<!-- screen: case-a -->
```text
The network: the interface you reach this machine on. More interfaces are added later, from the console menu.
Network ports:
  1) ens192       00:50:56:aa:bb:01  link up
  2) ens224       00:50:56:aa:bb:02  link up
  3) ens256       00:50:56:aa:bb:03  link up
Which port? [1]: 1
Does the network give this machine its address by itself (DHCP)? [y]: n
Address and prefix, for example 192.168.10.20/24: 10.0.30.5/24
Gateway, for example 192.168.10.1 (none if there is no way out of this network): 10.0.30.1
DNS servers, separated by spaces (at most three) [10.0.30.1]:
Network port:   ens192, up, gateway answers
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

Finish the other first-boot questions (host name, time, the product's administrator). When setup has finished and
the status screen is up, sign in as `rezadmin` and open the network menu:

```bash
sudo rezztor-setup network
```

The tool names this first interface **Interface 1**. It carries the default route. A gateway on a later interface
is only the next hop of the routes you name on that interface; it never becomes a second default route.

## 3. Add the second card (VLAN 100)

Choose **2) Add an interface**. VLAN 100 has no gateway. Leave the web interface and SSH on Interface 1 (the
default answer is no):

<!-- screen: case-a-menu -->
```text
Network
  1) Show the interfaces
  2) Add an interface
  3) Change an interface
  4) Remove an interface
  5) Routes: show, add, remove
  6) Test: do the gateways answer, and which way does an address leave?
  7) Restore the last settings that were kept
  0) Back
Choice [0]: 2
Not in this build: bonds, a second internet uplink, the same subnet in two VLANs, jump hosts,
inbound traps, syslog or TFTP, jumbo frames, and IPv6 on device networks.

This machine has one interface so far, now called Interface 1. With a second one, the product reaches
devices only in the networks named here: each interface's own network, and the routes on it.
A device network behind the gateway of Interface 1 is named under Routes, with that gateway.
Network ports:
  1) ens192       00:50:56:aa:bb:01  link up   used by Interface 1
  2) ens224       00:50:56:aa:bb:02  link up
  3) ens256       00:50:56:aa:bb:03  link up
Which port? [2]:
May the web interface and SSH answer on this interface? [n]:
Address and prefix, for example 10.20.30.10/24: 10.0.100.5/24
  A gateway here is the next hop of the routes you name on this interface. It never becomes a second default route.
Gateway on this network (none: no gateway):
Interface    Used for                Port    Address        Gateway    Reaches          State
Interface 1  Internet+Users+Devices  ens192  10.0.30.5/24   10.0.30.1  everything else  up, gateway answers
Interface 2  Devices                 ens224  10.0.100.5/24  none       10.0.100.0/24    up

Web interface:  https://10.0.30.5   (answers on Interface 1 only)
SSH:            Interface 1 only, while SSH is switched on
On your devices, allow management from:  10.0.30.5 (Interface 1),  10.0.100.5 (Interface 2)
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
The product itself cannot reach that network; only its device connections can. A monitoring or log
server inside it that the product may use is named under the last entry of this menu.
```

## 4. Add the third card (VLAN 200)

VLAN 200 has a gateway, and more device subnets behind it. Name those networks when asked, or add them later under
**Routes**. Without that name, the appliance will not send anything for them to this gateway.

<!-- screen: case-a-menu -->
```text
Network
  1) Show the interfaces
  2) Add an interface
  3) Change an interface
  4) Remove an interface
  5) Routes: show, add, remove
  6) Test: do the gateways answer, and which way does an address leave?
  7) Restore the last settings that were kept
  8) Services inside device networks that the product may use
  0) Back
Choice [0]: 2
Network ports:
  1) ens192       00:50:56:aa:bb:01  link up   used by Interface 1
  2) ens224       00:50:56:aa:bb:02  link up   used by Interface 2
  3) ens256       00:50:56:aa:bb:03  link up
Which port? [3]:
May the web interface and SSH answer on this interface? [n]:
Address and prefix, for example 10.20.30.10/24: 10.0.200.5/24
  A gateway here is the next hop of the routes you name on this interface. It never becomes a second default route.
Gateway on this network (none: no gateway): 10.0.200.1
Networks reached through that gateway, separated by spaces, for example 172.16.0.0/24 10.100.100.0/24
(none: add them later under Routes): 10.20.0.0/16
Interface    Used for                Port    Address        Gateway     Reaches                      State
Interface 1  Internet+Users+Devices  ens192  10.0.30.5/24   10.0.30.1   everything else              up, gateway answers
Interface 2  Devices                 ens224  10.0.100.5/24  none        10.0.100.0/24                up
Interface 3  Devices                 ens256  10.0.200.5/24  10.0.200.1  10.0.200.0/24, 10.20.0.0/16  up, gateway answers

Web interface:  https://10.0.30.5   (answers on Interface 1 only)
SSH:            Interface 1 only, while SSH is switched on
On your devices, allow management from:  10.0.30.5 (Interface 1),  10.0.100.5 (Interface 2),  10.0.200.5 (Interface 3)
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
The product itself cannot reach that network; only its device connections can. A monitoring or log
server inside it that the product may use is named under the last entry of this menu.
```

Once there are several interfaces, the menu also offers **8) Services inside device networks that the product may
use**, for a monitoring or log server that sits inside a device network and that the product itself should reach.
Press Enter there if there is none.

## 5. Check

Choose **1) Show the interfaces**. The table lists every interface with **up**. A warning under it says what did
not answer.

<!-- screen: case-a-menu -->
```text
Network
  1) Show the interfaces
  2) Add an interface
  3) Change an interface
  4) Remove an interface
  5) Routes: show, add, remove
  6) Test: do the gateways answer, and which way does an address leave?
  7) Restore the last settings that were kept
  8) Services inside device networks that the product may use
  0) Back
Choice [0]: 1
Interface    Used for                Port    Address        Gateway     Reaches                      State
Interface 1  Internet+Users+Devices  ens192  10.0.30.5/24   10.0.30.1   everything else              up
Interface 2  Devices                 ens224  10.0.100.5/24  none        10.0.100.0/24                up
Interface 3  Devices                 ens256  10.0.200.5/24  10.0.200.1  10.0.200.0/24, 10.20.0.0/16  up

Web interface:  https://10.0.30.5   (answers on Interface 1 only)
SSH:            Interface 1 only, while SSH is switched on
On your devices, allow management from:  10.0.30.5 (Interface 1),  10.0.100.5 (Interface 2),  10.0.200.5 (Interface 3)
```

- The line **On your devices, allow management from** gives the addresses to allow on your devices:
  [What to allow on your devices](appliance-what-to-allow-on-devices.md).
- Later: **6) Test** asks the gateways again, then asks for an address: it prints which interface and gateway that
  address leaves by, and whether it answers ping.

<!-- screen: case-a-menu -->
```text
Network
  1) Show the interfaces
  2) Add an interface
  3) Change an interface
  4) Remove an interface
  5) Routes: show, add, remove
  6) Test: do the gateways answer, and which way does an address leave?
  7) Restore the last settings that were kept
  8) Services inside device networks that the product may use
  0) Back
Choice [0]: 0
```

## If something goes wrong

| What you see | What to do |
| --- | --- |
| `the address 10.0.100.5 is already used by another machine (00:11:22:33:44:55)` | Another machine has this address. Choose a free one. Nothing was changed. |
| `Warning: ... the gateway 10.0.30.1 does not answer.` | A warning, not a refusal: some gateways do not answer. Check the address and the port group of this adapter. |
| `... overlaps ..., which the containers on this machine use.` | One of your networks uses the addresses the product's own containers use. Answer `y` when the tool offers another block. |
| `... overlaps the subnet ... of ...` | Two of your networks would lead to the same addresses. One address must lead to one place. |
| `The firewall refused these networks` | The appliance went back by itself. Write down the text and send it to support. |

## Not in this build

Bonds (two ports as one), a second internet uplink, the same subnet in two VLANs, jump hosts, traps, syslog or TFTP
sent **to** the appliance, jumbo frames, and IPv6 on device networks. The tool says so too, and refuses what it
cannot do with a sentence that names the problem.
