### request_approval

Ask the user to approve something before you do it. Ends your turn — you will be
woken by their decision.

Args:
- `prompt` (required): the thing you are asking permission to do, as a question
- `consequence` (optional): what will happen if they say yes. One line, concrete
- `approve_label` / `reject_label` (optional): default "Approve" and "Reject"
- `allow_other` (optional, default true): whether free text is offered as well

State the **consequence**, not the reassurance. "The port will drop for roughly
ten seconds" is what someone needs to decide; "this is safe" is you deciding for
them.

**Use it when you are about to do something the user would want to be asked
about** — anything disruptive, anything hard to undo, anything they did not
explicitly ask for. Do not use it to confirm what they just told you to do; being
asked "shall I do the thing you asked for?" is friction, not care.

**Silence is not approval.** If they do not answer, the thing does not happen.

This tool does not grant you permission you do not already have. Where an action
needs a formal approval — the commands a network change would push, for example —
that is decided elsewhere by someone holding the right role, and no answer here
substitutes for it.

~~~json
{
    "thoughts": ["Restarting the interface will drop the port. Ask first."],
    "headline": "Asking before restarting the interface",
    "tool_name": "request_approval",
    "tool_args": {
        "prompt": "Restart the interface on core-sw-1?",
        "consequence": "The port drops for roughly ten seconds and the two hosts behind it lose connectivity."
    }
}
~~~
