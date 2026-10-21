# Release keys

The public halves of the keys that sign update packages (ADR 0080), one file each, `<name>.pub`. The
file name is the key's name: the Updates page and the console show it.

Every ISO built from this repository copies the `*.pub` files here to `/opt/rezztor/keys/` on the
installed machine, and that folder is all the update tool trusts. An update may add a key, never
replace or remove one: a `*.pub` file new here since the base release goes into the next package
(`scripts/sign_release.py`), and the machine adds it once the new version runs, under a name it does
not have yet. So a third key is added by committing its public half here and signing the next update
with a key the machines already trust. Changing or deleting a file here changes nothing on a machine.

A private key never comes near this repository. Make a pair with

    python scripts/sign_release.py keypair --private-out /path/to/keys/release-2026a.pem \
        --public-out deploy/iso/keys/release-2026a.pub

and make two from the first day: one in use, one whose private half lies in a safe. With no `*.pub`
file here, a machine installed from the ISO accepts no update and its status screen says so, and a
release build stops unless `REZZTOR_ISO_NO_UPDATES=1` asks for exactly that.

## Support keys: `support/`

A public key in `support/<name>.pub` is a switch as well as a key (ADR 0083). An ISO that carries one
installs a machine whose console is **closed**: the account `rezadmin` gets the setup menu and nothing
else, and a root shell opens only from the menu's "Support access", for a code signed with the private
half:

    python scripts/sign_release.py keypair --private-out /path/to/keys/support-2026a.pem \
        --public-out deploy/iso/keys/support/support-2026a.pub
    python scripts/sign_release.py support-code --key /path/to/keys/support-2026a.pem \
        --challenge RZ-1A2B3C4D-ABCDEFGH

With no key in `support/`, every ISO installs the open console it always did, and a lab ISO never
closes. Whoever types a code is root on that machine until the shell is left: give one for a session
you take part in. Lose the private half and no closed machine can be opened again by anyone, so keep
it as the release keys are kept. It is a different key from the release keys on purpose: the person
who may open a customer's machine need not be the person who may sign an update.
