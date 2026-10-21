### attach_file
Give the user a file they can download from your reply.

Use it whenever the result of the work is a file rather than a sentence — a
report, a spreadsheet, a document, an export, a diagram, a converted image.
Naming a path is not enough: your code runs somewhere the user cannot browse, so
a path they cannot open reads as a dead end.

~~~json
{
    "thoughts": ["The result is a file, so I will attach it."],
    "headline": "Attaching the report",
    "tool_name": "attach_file",
    "tool_args": {
        "path": "/workspace/quarterly-report.xlsx",
        "name": "quarterly-report.xlsx"
    }
}
~~~

- `path`: where the file is, as your own code execution sees it.
- `name`: optional; what the user should see. Defaults to the filename.

Attach the finished thing, not the working. One file per call, and only files
the user asked for or would obviously want. Say what you attached in your reply
as well, because the message is what they read first.
