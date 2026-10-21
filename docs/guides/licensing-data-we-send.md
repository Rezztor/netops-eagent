# What licensing and updates send, and to whom

For a customer security review.

A Community build sends nothing. A source checkout, an image you built yourself and the old open lab image never
contact a licence server and never make the daily check-in. There is no seat, device or feature report to send.

A Rezztor Enterprise image is the one whose build stamp says it is Enterprise. It talks to two Rezztor hosts, over HTTPS only:
`https://license.rezztor.com` for the licence, and, on an appliance installed from the ISO,
`https://update.rezztor.com` for updates ([below](#updates-appliance-only)). There is no setting to change either
host. They receive the fields below and nothing else: no user names, no chat or file content, no inventory (a
count of devices, not the devices), no host name, no device addresses, no usage analytics.

An Enterprise image that has never been activated sends nothing. An offline Enterprise installation never calls the server.

## Activation (once per installation)

When: an administrator enters the activation key (`REZ-` and 25 characters). The product checks the key for
typing mistakes first.

| Field | What it is |
| --- | --- |
| `key` | The activation key. The server keeps only its hash. |
| `iid` | The installation id: random, made on first start, not derived from hardware or the network. |
| `version` | The product version. |
| `nonce` | Random bytes, new for every request, so an old answer cannot be replayed. |

Comes back: a signed licence (customer, edition, features, seats, device limit if it has one, contract end), a signed lease (30 days by
default), and an installation token. The token is a random secret the product keeps and sends with the later
calls, so nobody else can speak for this installation.

## Daily check-in

When: about once a day, in the background. A user never waits for it. A failed call is retried after 1, 5 and
30 minutes, then hourly.

| Field | What it is |
| --- | --- |
| `lic` | The licence number. |
| `ver` | The version of the licence document held. |
| `iid` | The installation id. |
| `token` | The installation token. |
| `seq` | The sequence number of the lease held. |
| `prev` | A hash of the lease held, not the lease itself. |
| `seats` | How many seats are in use, as a number. |
| `devices` | How many network devices are in use, as a number: active devices in the inventory, and Azure firewalls, load balancers and gateways. Not their names or addresses. |
| `version` | The product version. |
| `nonce` | As above. |

Comes back: a new signed lease, and a newer licence document only if one exists.

If the licence server is an older one that does not accept `devices`, the product sends the check-in again at once
without it, and leaves it out for a week.

## Release (only when you move the product)

When: an administrator chooses **Release this installation**. Sends `lic`, `iid`, `token` and `nonce`.

## Other requests

`GET /v1/keys` fetches the public keys that vouch for the server's signatures. It sends nothing. Every request
carries `User-Agent: RezztorEAgent/<version>`. Any HTTPS request also shows the source address of your server
or proxy and the time.

## What the answers can do

An answer changes what the product does only if it is a signed document that the product verifies itself.
Nothing executable comes back.

## If the server cannot be reached

Nothing stops at once. The product keeps working on the lease it holds, then through the grace period
(everything works, with a warning). With the defaults that is about 60 days from the last successful
check-in. After that the installation is Restricted: sign-in, reading, export, settings and the licence page
work; new agent runs, new device or cloud changes and scheduled tasks do not start. Nothing is deleted.

## Offline licences

With no internet access, an administrator makes a request code in **Settings -> Licence**. It holds the
installation id, the deployment name, the product version and a timestamp. It travels by e-mail, in a
person's hands, to Rezztor. Rezztor returns a licence file (`.rezlic`) that works for that installation only
and for one year at most. The product never contacts the server for it.

## Updates (appliance only)

The paid download path is for an Enterprise image. A Community build does not ask for update packages.

An Enterprise appliance installed from the ISO asks Rezztor's update service which update packages follow the version it runs.
The appliance's host makes the call (not the application), through the proxy named in its `.env` if there is one.
A Compose deployment you installed by hand never calls it.

When: once a day while **Check every day** is on (Settings, Updates; on by default), when an administrator presses
**Check now**, and when an administrator downloads an update (REZ-Pro and REZ-Ultra only).

| Request | Sends |
| --- | --- |
| `POST /v1/hello` | The installation's update key: the public half of a key made on the machine. |
| `POST /v1/session` | The answer to a challenge the service encrypted to that key: 64 hex characters, proving the machine holds the private half. The private half never leaves the machine. |
| `GET /v1/updates` | The running version, its core release, and the package formats this appliance's update tool can read, with the session token from the step before (valid 10 minutes). |
| `GET /v1/updates/<version>/file` | The version wanted and those formats, with the session token. |

Comes back: the list of packages after the running version (version, release date, size, notes, whether it is a
security fix, from when this plan may download it), and on a download the package itself. A package is installed
only after the appliance has checked Rezztor's signature on it itself, and only when an administrator asks. The
service records each call against the installation it belongs to.

Switch **Check every day** off to stop the daily call; the appliance then calls only when an administrator presses
**Check now** or downloads. Files from the customer portal can always be installed without any call.

## What you can check

- Allow outbound HTTPS to `license.rezztor.com`, and on an appliance installed from the ISO also to
  `update.rezztor.com`, and nothing else.
- The licence requests are `POST` JSON bodies of 4 KB at most. You can capture and read them with a proxy, and
  the update requests too.
- The product follows no redirect from the licence server, and it sends the installation token over HTTPS only.
