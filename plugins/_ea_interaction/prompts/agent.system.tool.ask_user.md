### ask_user

Ask the user a question and give them answers to click. Ends your turn — you will
be woken by their reply, so do not plan anything after calling it.

Args:
- `question` (required): one sentence, the thing you actually need to know
- `options` (required): **two to four** answers. Either plain strings, or objects with `label` and `detail`
- `context` (optional): a line of framing shown above the options
- `allow_other` (optional, default true): whether free text is offered as well

Put the **consequence** in `detail`, not a recommendation. "21 devices — over the
10-device job cap" tells the user why they might not want it; "not recommended"
asks them to take your word for it.

**Ask only when the answer changes what you do.** If you can decide it yourself,
decide it. If the user has already said, act on what they said. If the answer is
genuinely open-ended, ask in prose instead — four options that do not contain the
real answer are worse than a plain question.

~~~json
{
    "thoughts": ["Three inventory groups and the user did not say which."],
    "headline": "Asking which switches to gather from",
    "tool_name": "ask_user",
    "tool_args": {
        "question": "Which switches should I gather facts from?",
        "options": [
            {"label": "Core switches", "detail": "core-sw-1, core-sw-2"},
            {"label": "Access switches", "detail": "14 devices in access_switches"},
            {"label": "Everything", "detail": "21 devices — over the 10-device job cap"}
        ]
    }
}
~~~
