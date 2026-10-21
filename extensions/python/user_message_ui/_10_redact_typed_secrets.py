"""Replace a password typed into chat with a typed token before it is logged,
stored in history or sent to the model. The secure credential form is the only
way to give the agent a device secret.

ponytail: a failure here logs and lets the message through (a broken guard must
not break the chat), and the heuristics are regexes, so a secret in free prose
("it's the name of my dog") is not caught. Upgrade path: a classifier.
"""

import re

from helpers.extension import Extension
from helpers.print_style import PrintStyle

WARNING = (
    "I removed what looked like a password from your message. "
    "Use the secure credential form instead."
)

_KW = r"(?:password|passwd|pwd|pw|passphrase|secret|token|api[ _-]?key|community(?:[ _-]string)?|psk|pre-shared[ _-]key)"
# "is"/"are" also open ordinary sentences ("the password is required"), so those words are not values.
_PROSE = (
    "a|an|the|not|no|in|on|at|to|too|and|or|for|from|now|still|already|here|there|set|stored|saved|valid|"
    "invalid|correct|incorrect|wrong|right|required|expired|missing|empty|working|failing|rejected|changed|"
    "reset|stuck|enabled|disabled|same|different|ok|fine|being"
)
_TOKEN = r"(?!\[REDACTED)"
_CONVERSATIONAL = re.compile(
    rf"(?P<pre>\b{_KW}\b(?:[ \t]+(?:for|of|on)[ \t]+[^\s:=,;]+)?[ \t]*(?:[:=]|\b(?:is|are)\b(?!\s+(?:{_PROSE})\b))[ \t]*){_TOKEN}"
    r"""(?P<val>"[^"\n]*"|'[^'\n]*'|[^\s"',;]+)""",
    re.I,
)


def redact_typed(text: str) -> tuple[str, dict[str, int]]:
    """Clean text and a count of what was removed, by type."""
    from plugins._ea_inventory.helpers.redact import redact_text

    n = 0

    def sub(m: re.Match) -> str:
        nonlocal n
        n += 1
        return m["pre"] + "[REDACTED:password]"

    out, counts = redact_text(_CONVERSATIONAL.sub(sub, text))
    if n:
        counts["password"] = counts.get("password", 0) + n
    return out, counts


class RedactTypedSecrets(Extension):

    def execute(self, data: dict | None = None, **kwargs):
        try:
            message = (data or {}).get("message")
            if not isinstance(message, str) or not message:
                return
            out, counts = redact_typed(message)
            if not counts:
                return
            data["message"] = out
            PrintStyle.warning(f"Input guard removed typed secrets from a user message: {counts}")
            try:
                from plugins._audit.helpers import audit

                audit.emit("input_guard.redacted", counts=counts)
            except Exception:
                pass
            if self.agent:
                self.agent.context.log.log(type="warning", heading="Input guard", content=WARNING)
        except Exception as e:
            PrintStyle.error(f"Input guard failed, message passed through: {type(e).__name__}")
