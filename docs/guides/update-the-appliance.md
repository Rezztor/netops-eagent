# Update the appliance

A fix from Rezztor arrives as one file, an update package. You do not install the appliance again.

This guide is for a machine installed from the Rezztor EAgent ISO. A Compose deployment you installed by hand
has no update tool; it is upgraded as `INSTALL.md` in its bundle says.

## How updates are released

- A **core release** is an ISO. New installations start from the current one.
- Every change after it is an **update package** on that core. After the 10th package, or 3 months after the
  core (whichever comes first), Rezztor releases a new core. A machine you already installed does not need the
  new ISO: the new core reaches it as one more package.
- Packages go in **strictly in order**, oldest first. Each one names the version it follows. Every package stays
  available, so a machine that was offline for a long time can still catch up one step at a time.

## Two ways to get an update

- **From the Updates page** (REZ-Pro and REZ-Ultra): the appliance asks Rezztor's update service,
  `update.rezztor.com`, which packages follow the version it runs, and you press **Download and install**.
- **As a file**, on every plan and on a machine that does not reach the internet: download the package from the
  customer portal and upload it on the Updates page, or put it on the console.

Either way the appliance installs only a file that Rezztor signed, and it checks the signature itself. A file that
was changed on the way is refused. Nothing is installed without an administrator.

## What the appliance sends to the update service

Once a day, and when you press **Check now**, the appliance's host asks `update.rezztor.com` over HTTPS (through
your proxy, if `.env` names one). It sends its update key (the public half, below), its version and its core
release, and proves it holds the private half of that key by decrypting a random challenge. Nothing else: no users,
no chats, no device data. The full list is in [What licensing sends](licensing-data-we-send.md).

Allow outbound HTTPS to `update.rezztor.com`, as well as to `license.rezztor.com`. For a site that must not reach
the internet, switch **Check every day** off on the Updates page; then the appliance asks only when somebody presses
**Check now**, and files still work.

## What you get from Rezztor as a file

- one file, `rezztor-eagent-update-<version>.rezupd` (about 10 MB for a normal fix; a new core is several GB);
- its SHA-256, to compare after the download;
- a few lines on what changes.

## Once: register the update key

Every appliance has an update key of its own. Rezztor needs it once, to make files that your machine can open and
to know the machine when it asks for updates. It is not a secret.

- In the browser: **Settings, Updates**, the line "Update key of this installation", button **Copy**.
- On the console: `sudo rezztor-update key`

Sign in to the customer portal (`portal.rezztor.com`), open the installation under **Installations**, and paste the
key there. Until you do, the Updates page says that Rezztor's update service does not know this installation yet.

## From the list (REZ-Pro, REZ-Ultra)

Sign in as an administrator and open **Settings, Updates**. **Available updates** lists every update that follows
the version you run, oldest first: version, release date, size, a **security** mark when it fixes one, what changes,
and from when your plan may download it. **Last checked** says when the appliance last asked; **Check now** asks
again. When the next update is in a format this appliance's update tool cannot read, the page does not show an
empty list. It says: "Update <version> needs a newer update tool. Install the bridge package by hand, or contact
support."

1. **Download and install** on the first update. The page shows the download, checks the file as it would an
   uploaded one, and installs it beside the running version. Nobody is interrupted. Then, as for a file (below),
   press **Restart now** when the page says **Restart to finish**.
2. The next update in the list then gets the button.

With more than one update waiting, **Update to latest (N)** does them all, one after another, in order: download,
install, restart, and on to the next. The page says how far it is ("3 of 7"). The application is away for about a
minute at each restart. If one update does not start, the appliance goes back to the version before it and stops
there.

Every administrator sees a line at the top of the product while updates are available ("2 updates available").

**On REZ-Free** the page shows the same list with "Upgrade to REZ-Pro to install from here". Each update can be
downloaded from the customer portal from its availability date (security fixes on the day they are released, the
rest 90 days after) and installed as a file. **When the licence has lapsed** the page says "Renew to receive
updates".

## In the browser, from a file

Sign in as an administrator and open **Settings, Updates**, **Install an update from a file**.

1. **Choose file…** and pick the `.rezupd` file. The page shows the version, what changes, whether the database
   changes, and the name of the key that signed it.
2. **Install**. Nobody is interrupted: the new version is prepared beside the running one. This takes about
   20 seconds.
3. What happens next depends on the update:
   - Only texts and screens are new for your machine: the page says "No restart was needed", and that is all.
   - Otherwise the page says **Restart to finish**, and every administrator sees a line at the top of the
     product until it is done. Press **Restart now** when it suits you. The application is away for about a
     minute (34 seconds on our test machine). People stay signed in; agent runs in progress stop.
4. The page comes back by itself and says "Updated to <version>".

## Debian updates and a restart of the appliance

An update can also bring the security fixes Debian has published for the appliance's operating system since its
ISO, the kernel among them. The list and the update card say so: "Debian security updates: 12 packages, restart of
the appliance needed".

- They are installed **after** the new version of Rezztor EAgent has started and answered, from the update file
  itself. Nothing is downloaded from Debian, and the appliance needs no access to Debian's servers.
- If they cannot be installed, the new version keeps running and the page says why. The next update brings the same
  fixes again. Send the message to Rezztor.
- When the kernel or another core part of the system changed, the Updates page shows **Restart the appliance**. The
  appliance does not restart by itself. Press the button when it suits you: the appliance is away for a few minutes
  and agent runs in progress stop. **Update to latest** installs every update
  first and leaves the restart to you at the end.
- On the console: `sudo rezztor-update reboot`.

**If the new version does not start**, you do nothing. The appliance waits (ten minutes at most), then returns
to the version you had, puts the database back when the new version had changed it, and the page says so:

```text
Version 2026.10.18 did not answer within 2 minutes after the restart. The appliance returned to
2026.10.17. Nothing was lost.
```

Send that message to Rezztor.

## On the console

Use this when the file is large, or when the browser cannot reach the product. Put the file on a USB stick or a
virtual CD and attach it to the machine, or copy it to the machine with SSH. Then sign in as `rezadmin` and run
`sudo rezztor-update` (or choose **update** in the menu of `sudo rezztor-setup`):

```text
Looking for update files on USB sticks, virtual CDs and in the inbox ...
  1) rezztor-eagent-update-2026.10.17.rezupd  (9.8 MB)
  Update 2026.10.17 for release 2026.10.08, 9.8 MB
  Signed by Rezztor (release-2026a)
  - Models: the context length of Ollama Cloud models is now found.
  The database does not change.
  A restart of the application is needed, unless only texts and screens are new for this machine.
Install it? [y/N] y
Installing. The product keeps running.
Version 2026.10.17 is installed beside the running version.
Restart the application now? [y/N] y
Restarting. This takes about a minute; if the new version does not start, the old one comes back.
Updated to 2026.10.17. The restart took 34 seconds.
```

With a file you copied to the machine: `sudo rezztor-update install /home/rezadmin/<file>`, then
`sudo rezztor-update restart`. `sudo rezztor-update status` shows the version, the keys the machine trusts, its
update key, and how the last update ended.

## When a file is refused

| The page or the console says | What it means |
| --- | --- |
| "its signature does not match" | The file was not made by Rezztor, or it was changed on the way. Download it again and compare the SHA-256. |
| "This update is ... You run ..., which is newer" | The file is older than what you run. There is no way back to an older version. |
| "This update needs release ..." | The file was made for another ISO release. Tell Rezztor which version your Updates page shows. |
| "This installation cannot open the file" | The file was not made for this machine. Send Rezztor the update key shown in the message. |
| "... are needed and ... are free" | The disk is too full. |
| "... is installed and waits for a restart" | Restart, or remove that update, before you upload another. |
| "Rezztor's update service does not know this installation yet" | Register the update key in the customer portal (above), then press **Check now**. |
| "An update between the version you run and the ones listed is missing" | Updates go in strictly in order and one is missing from the list. Tell Rezztor; install the missing one as a file. |
| "Your plan does not include installing updates from here" | REZ-Free or a lapsed licence. Download the update from the customer portal and install it as a file. |
| "... updates installed, then: ..." | **Update to latest** stopped at that update, for the reason after "then". The ones before it are installed. |
| "Rezztor's update service could not be reached" | The appliance cannot reach `update.rezztor.com` over HTTPS. Check the firewall and the proxy. Files still work. |
| "The database could not be backed up, so nothing was changed" | Nothing was switched. Send the message to Rezztor. |
| "This update was made for a newer update tool than this machine has" | Install the update that comes before it first: it brings the newer update tool. |
| "Update … needs a newer update tool" | The next update is in a format this appliance's update tool cannot read. Install the bridge package by hand, or contact support. |
| "The Debian updates could not be installed" | The new version runs; only Debian's fixes are missing. The next update brings them again. Send the message to Rezztor. |

## Before a large update

Take a snapshot of the virtual machine first when Rezztor's notes say the database changes. The appliance backs
the database up itself before every restart and restores it when it goes back, but a snapshot is the one
protection that does not depend on the appliance.

## A machine installed before October 2026

An appliance installed from the ISO of 2026-10-08 or earlier has no update tool yet. Rezztor sends one more
file for it, once: `rezztor-update-bootstrap-<date>.tar`, with its SHA-256.

1. On the console, switch SSH on (`sudo rezztor-setup`, entry **ssh**) and copy the file to the machine, or
   bring it on a USB stick.
2. Compare the checksum: `sha256sum rezztor-update-bootstrap-<date>.tar`
3. Run it:

   ```bash
   tar -xf rezztor-update-bootstrap-<date>.tar
   sudo sh rezztor-update-bootstrap/install.sh
   ```

4. It prints the machine's update key. Send that line to Rezztor.
5. The first update after that goes in on the console, as above. It brings the Updates page with it.

## What is not there yet

Older kernels stay on the disk after a new one is installed; a new ISO removes them. Nothing is installed without a
person: the daily check only fetches the list.
