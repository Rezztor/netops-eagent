# Activate your licence

A Community build — a source checkout, an image you built yourself, or the old open lab image — needs no
licence. Settings, Licence says **Community build: free, no limits.** Nothing is sent to a licence server.
Rezztor Enterprise (signed images, the update channel and support) is at <https://rezztor.com>.

The rest of this page is for a Rezztor Enterprise image, whose build stamp says it is one.

For administrators of an Enterprise image. You need the activation key Rezztor sent you: `REZ-` followed by five groups of five
characters, for example `REZ-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX`.

## Online

1. Sign in as an administrator and open **Settings -> Licence**.
2. Type the key into **Activation key**. The product checks it for typing mistakes before it sends anything.
3. Press **Activate**.
4. The page shows the state **Licensed**, with the customer, edition, seats, devices, features and contract end.

**Devices** is the number of network devices in use against what the licence allows: active devices in the
inventory, and Azure firewalls, load balancers and gateways. REZ-Free allows 100 and REZ-Pro and REZ-Ultra 500,
unless your licence names another number; a trial has no limit. From 90% the page shows a notice, and over the
limit a warning. Nothing stops working over the limit.

The server must be reachable over HTTPS at `https://license.rezztor.com`. The address cannot be changed.
Once a day the product checks in by itself and gets a new lease. Nobody waits for it. **Check in now** does it
on demand.

## Offline (no internet access)

1. Open **Settings -> Licence** and press **No internet access?**.
2. Type a **Deployment name**, for example the site or server name.
3. Press **Make request code**, then **Copy request code**.
4. Send the code to Rezztor.
5. Rezztor sends back a licence file (`.rezlic`).
6. Under **Licence file**, choose the file or paste its text.
7. Press **Load licence file**.

The file is verified for this installation before it is stored. A file made for another installation is
refused. An offline licence is valid for one year at most. Renew it with the same steps. An offline
installation never calls the licence server.

## What the states mean

| State | What happens |
| --- | --- |
| Licensed | Everything works. |
| Grace | The lease ended less than 14 days ago. Everything works. Administrators see a warning. |
| Grace, final period | Everything works. Every user sees a warning. It lasts up to the licence's grace days (30 by default). |
| Restricted | Sign-in, reading, export, settings and the licence page work. New agent runs, new device or cloud changes and scheduled tasks do not start. Nothing is deleted. |

An Enterprise image with no licence is Restricted. If the server cannot be reached, the product keeps working on
the lease it holds and then through the grace period: with the defaults, about 60 days from the last
successful check-in.

## Move to another server

1. On the old server, open **Settings -> Licence** and press **Release this installation**. Confirm.
2. On the new server, activate the same key as above.

The old installation is then Restricted until it is activated again. A licence allows as many releases in
90 days as it has installations. Beyond that, ask Rezztor.

## When a warning shows

1. Read the page: it shows the last check-in and its result.
2. Check that this server can reach `https://license.rezztor.com` over HTTPS (proxy, firewall, DNS).
3. Press **Check in now**.
4. If it still fails, or the state is Restricted, ask Rezztor. For an offline licence, make a new request
   code and load the new file.

What the product sends is listed in [What licensing sends, and to whom](licensing-data-we-send.md).
