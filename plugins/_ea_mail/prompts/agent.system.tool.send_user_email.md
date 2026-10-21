### send_user_email
Email someone who holds an account on this instance.

Use it for reminders, updates, follow-ups and handovers — anything the person
should still have after the conversation is closed, or that is meant for someone
who is not reading this chat.

`to` is the **username of an account here**, never an email address. The address
is read from that account; you cannot see it and you cannot choose it. There is
no way to reach anyone outside this instance, so do not try to write one in.

You may email the account this conversation belongs to. Any other account
requires the operator to be an administrator; you will be told if it is refused.

Write the body as the person would sign it — plain sentences, no markdown. A
line naming who sent it is appended for you; do not write your own.

Never put a password, a token, an API key or a reset link in a message. If
someone cannot sign in, tell them to use “Forgot password?” on the sign-in page.

~~~json
{
    "thoughts": ["The report is ready and Priya asked to be told when it was."],
    "headline": "Emailing Priya the update",
    "tool_name": "send_user_email",
    "tool_args": {
        "to": "priya",
        "subject": "Q3 reconciliation is finished",
        "body": "Hi Priya,\n\nThe Q3 reconciliation is done — three mismatches, all in the September batch, listed in the attached note.\n\nI will pick up the follow-up on Monday unless you want it sooner."
    }
}
~~~
