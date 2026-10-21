# What to allow on your devices, and what the appliance never does

For the network engineer who has to open the way between the appliance and the devices it manages.

> **Pictures are still to be added.** The console screens below are the tool's own text, copied from a run of it
> (a test compares them with the tool on every change). Screenshots from a real run will replace this note.

## The address to allow

The appliance has its own address in every device network. That address, and no other, is where its connections to
your devices come from. The console shows it (menu *Network*, entry 1 *Show the interfaces*, or
`sudo rezztor-setup network list`):

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

| Where the device is | The address it sees | In this example |
| --- | --- | --- |
| In a device network the appliance is in | The appliance's address in that network | A switch in VLAN 100 sees 10.0.100.5 |
| In a subnet behind that network's gateway | The appliance's address in the network of the gateway | A firewall at 10.20.0.9 sees 10.0.200.5 |

## What to allow

1. **On each device:** management from the appliance's address, on the port you manage the device with: SSH (TCP 22),
   or HTTPS (TCP 443) for a firewall that is managed over its API. Example for Cisco IOS, VLAN 100:

   ```
   ip access-list standard MGMT-ACCESS
    permit host 10.0.100.5
   line vty 0 15
    access-class MGMT-ACCESS in
    transport input ssh
   ```

2. **On the gateway of a device network** (VLAN 200 in the example): a way back to the appliance's address
   (10.0.200.5) from every subnet behind it. If a firewall sits between, allow 10.0.200.5 to the devices on their
   management port.
3. **Nothing towards the appliance.** Your devices never need to open a connection to it. You may ping it: it answers
   ping in every network, and that is the first test ("can the switch ping 10.0.100.5?").
4. **For the users:** HTTPS (TCP 443) to the appliance's address in the users' network, here `https://10.0.30.5`.

## What the appliance never does

- **It is not a router between your networks.** Nothing that arrives from one of your networks is passed on to
  another, in any direction. A device cannot reach the internet or the users' network through it, and a user cannot
  reach a device through it.
- **It answers nothing but ping in a device network.** The web interface and SSH do not answer there, on any of its
  addresses.
- **Only its device connections enter a network that is for devices only.** The part that runs code written by the
  model, and the web search, can reach public internet addresses only: never a device network and never another
  private address of yours.
- **The product itself does not enter a network that is for devices only**, except the services you named one by one
  on the console (a monitoring or log server), each by address and port. A network that is also for users or the
  internet stays reachable for it, so a model server or a sign-in server on your own network works.
- **It does not listen for traps, syslog or TFTP.** A device cannot send it anything in this build.
- **No IPv6 on device networks.** It ignores router advertisements there, so a device network can never become its
  way out.
- **It does not change your network by itself.** Networks are changed by a person on its console, and every change
  must be confirmed within 120 seconds or it is undone.

## Not in this build

Bonds (two ports as one), a second internet uplink, the same subnet in two VLANs, jump hosts, traps, syslog or TFTP
sent **to** the appliance, jumbo frames, and IPv6 on device networks. The tool says so too, and refuses what it
cannot do with a sentence that names the problem.
