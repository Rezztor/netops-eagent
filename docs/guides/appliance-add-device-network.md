# Add a device network to the appliance later

Networks are changed on the appliance's console (the screen of the virtual machine, or the server's monitor), or
over SSH if you switched SSH on. The web interface shows the networks and never changes them.

> **Pictures are still to be added.** The console screens below are the tool's own text, copied from a run of it
> (a test compares them with the tool on every change). Screenshots from a real run will replace this note.

## Before you start

- **vCenter, one card per port group:** add a network adapter to the virtual machine first, connected to the new
  port group, and write down its MAC address. Adding it while the machine runs has not been tested; if the new port
  does not appear in the list, restart the appliance.
- **A trunk (vCenter trunk port group, or a physical server):** allow the new VLAN on the port group or on the switch
  port first (`switchport trunk allowed vlan add 200` on Cisco IOS).
- Have ready: a free address in the new network with its prefix, the gateway if there is one, and the subnets behind
  that gateway (or add those later under Routes).

## 1. Open the menu

Sign in as `rezadmin` and run:

```bash
sudo rezztor-setup network
```

<!-- screen: two-cards -->
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
```

## 2. Add an interface

The example below starts with DHCP on the first card. The second card is a device network at
10.20.30.10/24, with gateway 10.20.30.1, and two networks behind that gateway: 172.16.0.0/24 and 10.100.100.0/24.
Choose the free port, leave the web interface and SSH off on this interface (the default), type the address, the
gateway, and the networks it reaches.

<!-- screen: two-cards -->
```text
This machine has one interface so far, now called Interface 1. With a second one, the product reaches
devices only in the networks named here: each interface's own network, and the routes on it.
A device network behind the gateway of Interface 1 is named under Routes, with that gateway.
Network ports:
  1) ens192       00:50:56:aa:bb:01  link up   used by Interface 1
  2) ens224       00:50:56:aa:bb:02  link up
Which port? [2]:
May the web interface and SSH answer on this interface? [n]:
Address and prefix, for example 10.20.30.10/24: 10.20.30.10/24
  A gateway here is the next hop of the routes you name on this interface. It never becomes a second default route.
Gateway on this network (none: no gateway): 10.20.30.1
Networks reached through that gateway, separated by spaces, for example 172.16.0.0/24 10.100.100.0/24
(none: add them later under Routes): 172.16.0.0/24 10.100.100.0/24
Interface    Used for                Port    Address                  Gateway       Reaches                                        State
Interface 1  Internet+Users+Devices  ens192  192.168.10.57/24 (DHCP)  192.168.10.1  everything else                                up, gateway answers
Interface 2  Devices                 ens224  10.20.30.10/24           10.20.30.1    10.20.30.0/24, 172.16.0.0/24, 10.100.100.0/24  up, gateway answers

Web interface:  https://192.168.10.57   (answers on Interface 1 only)
SSH:            Interface 1 only, while SSH is switched on
On your devices, allow management from:  192.168.10.57 (Interface 1),  10.20.30.10 (Interface 2)
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
```

That gateway is only the next hop of the routes named on this interface. It never becomes a second default route:
exactly one interface (the one from first boot) has the default route. If a second default were added, answers
would leave by the wrong interface and be dropped; the appliance also checks the reverse path strictly, so a
reply that came in on the wrong port would still fail.

A new interface carries devices. The tool names it (Interface 2, or VLAN 200 on a tagged port). On a trunk, where
the port is already in use, the tool also asks for the VLAN ID of the new network, straight after the port.

Entry 1 shows the same table again whenever you like:

<!-- screen: two-cards -->
```text
Choice [0]: 1
Interface    Used for                Port    Address                  Gateway       Reaches                                        State
Interface 1  Internet+Users+Devices  ens192  192.168.10.57/24 (DHCP)  192.168.10.1  everything else                                up
Interface 2  Devices                 ens224  10.20.30.10/24           10.20.30.1    10.20.30.0/24, 172.16.0.0/24, 10.100.100.0/24  up

Web interface:  https://192.168.10.57   (answers on Interface 1 only)
SSH:            Interface 1 only, while SSH is switched on
On your devices, allow management from:  192.168.10.57 (Interface 1),  10.20.30.10 (Interface 2)
```

## 3. What changes when one interface becomes several

With a second interface, the product reaches devices only in the networks named: each interface's own network and
the routes on it. A device network behind the gateway of Interface 1 must be named under Routes with that gateway.
Until it is named, the appliance's firewall does not let the product's device connections through to it.

## 4. Web interface and SSH on the new interface

The web interface and SSH stay off on a new interface unless you answer yes. When they are on, they answer users in
that interface's own network and in the networks its routes name. In the example above they stay on Interface 1
only.

## 5. Routes

Entry 5 lists every route (Interface, Reaches, Gateway) and adds or removes one at a time. Here a further network
is added on Interface 2, then one on Interface 1 behind another gateway of that first network, then that one is
removed.

<!-- screen: routes -->
```text
Choice [0]: 5

Routes
Interface    Reaches          Gateway
Interface 1  everything else  192.168.10.1
Interface 2  172.16.0.0/24    10.20.30.1
Interface 2  10.100.100.0/24  10.20.30.1
  1) Add a route
  2) Remove a route
  0) Back
Choice [0]: 1
  1) Interface 1
  2) Interface 2
Which interface? [1]: 2
Network to reach, with its prefix, for example 172.16.0.0/24: 10.50.0.0/16
Gateway it is behind, an address in this interface's own network [10.20.30.1]:
Interface    Used for                Port    Address                  Gateway       Reaches                                                      State
Interface 1  Internet+Users+Devices  ens192  192.168.10.57/24 (DHCP)  192.168.10.1  everything else                                              up, gateway answers
Interface 2  Devices                 ens224  10.20.30.10/24           10.20.30.1    10.20.30.0/24, 172.16.0.0/24, 10.100.100.0/24, 10.50.0.0/16  up, gateway answers

Web interface:  https://192.168.10.57   (answers on Interface 1 only)
SSH:            Interface 1 only, while SSH is switched on
On your devices, allow management from:  192.168.10.57 (Interface 1),  10.20.30.10 (Interface 2)
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
```

A route on the first interface may use another gateway of that network:

<!-- screen: routes -->
```text
Routes
Interface    Reaches          Gateway
Interface 1  everything else  192.168.10.1
Interface 2  172.16.0.0/24    10.20.30.1
Interface 2  10.100.100.0/24  10.20.30.1
Interface 2  10.50.0.0/16     10.20.30.1
  1) Add a route
  2) Remove a route
  0) Back
Choice [0]: 1
  1) Interface 1
  2) Interface 2
Which interface? [1]: 1
Network to reach, with its prefix, for example 172.16.0.0/24: 10.60.0.0/16
Gateway it is behind, an address in this interface's own network [192.168.10.1]: 192.168.10.254
Interface    Used for                Port    Address                  Gateway       Reaches                                                      State
Interface 1  Internet+Users+Devices  ens192  192.168.10.57/24 (DHCP)  192.168.10.1  everything else                                              up, gateway answers
Interface 2  Devices                 ens224  10.20.30.10/24           10.20.30.1    10.20.30.0/24, 172.16.0.0/24, 10.100.100.0/24, 10.50.0.0/16  up, gateway answers

Web interface:  https://192.168.10.57   (answers on Interface 1 only)
SSH:            Interface 1 only, while SSH is switched on
On your devices, allow management from:  192.168.10.57 (Interface 1),  10.20.30.10 (Interface 2)
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
```

Remove one when it is no longer needed:

<!-- screen: routes -->
```text
Routes
Interface    Reaches          Gateway
Interface 1  everything else  192.168.10.1
Interface 1  10.60.0.0/16     192.168.10.254
Interface 2  172.16.0.0/24    10.20.30.1
Interface 2  10.100.100.0/24  10.20.30.1
Interface 2  10.50.0.0/16     10.20.30.1
  1) Add a route
  2) Remove a route
  0) Back
Choice [0]: 2
  1) 10.60.0.0/16 through 192.168.10.254 (Interface 1)
  2) 172.16.0.0/24 through 10.20.30.1 (Interface 2)
  3) 10.100.100.0/24 through 10.20.30.1 (Interface 2)
  4) 10.50.0.0/16 through 10.20.30.1 (Interface 2)
Which route? [1]: 1
Remove the route to 10.60.0.0/16? [n]: y
Interface    Used for                Port    Address                  Gateway       Reaches                                                      State
Interface 1  Internet+Users+Devices  ens192  192.168.10.57/24 (DHCP)  192.168.10.1  everything else                                              up, gateway answers
Interface 2  Devices                 ens224  10.20.30.10/24           10.20.30.1    10.20.30.0/24, 172.16.0.0/24, 10.100.100.0/24, 10.50.0.0/16  up, gateway answers

Web interface:  https://192.168.10.57   (answers on Interface 1 only)
SSH:            Interface 1 only, while SSH is switched on
On your devices, allow management from:  192.168.10.57 (Interface 1),  10.20.30.10 (Interface 2)
Keep these settings?  Type keep or back, then Enter.   Going back by itself in 120 s
keep
Kept.
```

The tool refuses a route that overlaps another network, another route, or the containers' address block; a gateway
outside the interface's own network; and 0.0.0.0/0 (there is already one default route). Each refusal is one
sentence that names both sides, and nothing is changed. A route that would change the way back to your own SSH
session is not refused: the tool names your address and asks before it applies the route.

## 6. Test

Entry 6 asks every gateway again, then asks for an address. It prints which interface and gateway that address
leaves by, and whether it answers ping.

<!-- screen: routes -->
```text
Choice [0]: 6
Interface    Used for                Port    Address                  Gateway       Reaches                                                      State
Interface 1  Internet+Users+Devices  ens192  192.168.10.57/24 (DHCP)  192.168.10.1  everything else                                              up, gateway answers
Interface 2  Devices                 ens224  10.20.30.10/24           10.20.30.1    10.20.30.0/24, 172.16.0.0/24, 10.100.100.0/24, 10.50.0.0/16  up, gateway answers

Web interface:  https://192.168.10.57   (answers on Interface 1 only)
SSH:            Interface 1 only, while SSH is switched on
On your devices, allow management from:  192.168.10.57 (Interface 1),  10.20.30.10 (Interface 2)
An address to try: which interface does it leave by? (Enter to skip): 172.16.0.9
172.16.0.9 leaves through ens224 (Interface 2), gateway 10.20.30.1, from 10.20.30.10.
It answers ping.
```

Then allow the new address on the devices of that network:
[What to allow on your devices](appliance-what-to-allow-on-devices.md).

## 7. Keep or go back in 120 seconds, and Restore

Every change ends with the same question: **Keep these settings?** Type `keep` and press Enter within 120 seconds.
If you type `back`, type nothing, or lose the console, the appliance goes back by itself to the settings from before.
The timer does not belong to your session, so a closed window does not stop it.

**Restore the last settings that were kept** (entry 7) goes back to the last state somebody answered `keep` to.

Adding an interface or a route does not restart the product. An interface that takes its address from DHCP asks for
it again during the change, which takes about a second; connections that are open stay open.

## Changing or removing an interface

- **Change** (entry 3) asks the same questions with your earlier answers in brackets. Press Enter to keep one; type
  `none` to empty one.
- **Remove** (entry 4) asks once more before it removes the interface.
- If you change or remove the interface **your own session comes through**, the tool names the address you are
  connected from and asks twice. If the new settings cut you off, wait: after 120 seconds the settings from before
  are back.
- When there are several interfaces, entry 8 names services inside device networks that the product may use
  (a monitoring or log server), each by address and port.

## Without the console: an answers file

The same change from a file, for an unattended setup. The file lists **all** networks, not only the new one:

```bash
sudo rezztor-setup network check networks.env    # says what is wrong; changes nothing
sudo rezztor-setup network apply networks.env    # applies, then asks "Keep these settings?"
```

`rezztor-setup network --help` shows the keys of the file. Two additions for routes:

- **One network** (the flat answers file): `NET_ROUTES` names the networks behind a gateway of that interface, for
  example `NET_ROUTES=10.50.0.0/16@192.168.10.254`.
- **A route behind another gateway** is written as `SUBNET@GATEWAY` on `NETn_ROUTES` as well, for example
  `NET1_ROUTES=10.60.0.0/16@192.168.10.254`. Ordinary routes stay as space-separated prefixes
  (`NET2_ROUTES=172.16.0.0/24 10.100.100.0/24`).

`NETn_ROLES`, `NETn_NAME` and `NETn_TEST` still work in a file. The console no longer asks for a role, a name or a
test device.

## Not in this build

Bonds (two ports as one), a second internet uplink, the same subnet in two VLANs, jump hosts, traps, syslog or TFTP
sent **to** the appliance, jumbo frames, and IPv6 on device networks. The tool says so too, and refuses what it
cannot do with a sentence that names the problem.
