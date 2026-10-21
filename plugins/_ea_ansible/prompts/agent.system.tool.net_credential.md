### net_credential

Get a device credential stored. This is the **only** way to obtain one: it shows the user a secure form that saves the value straight to the credential store. You never receive it, cannot read it back, and must **never ask for a password, key or token in chat** (if the user types one anyway, tell them to use the form).

Args:
- `action`: `request` (the only action)
- `name` (required): the credential name, as the pre-check or Inventory shows it (letters, digits, `-`, `_`)
- `kind` (optional): `password` (default), `ssh_key`, `api_token` or `enable`
- `username` (optional): pre-fills the form's username

Use it when a pre-check says a credential is missing, or the user wants to set or replace one. It ends your turn. When the user replies "Credential <name> stored." continue with what you were doing, for example requesting the read again.

~~~json
{
    "thoughts": ["lab-sonic has no stored credential. Asking for it through the secure form, not in chat."],
    "headline": "Showing the secure credential form",
    "tool_name": "net_credential",
    "tool_args": {"action": "request", "name": "lab-sonic", "username": "admin"}
}
~~~
