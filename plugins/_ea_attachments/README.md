# Attachments

Lets the agent hand the user a file they can download, instead of naming a path.

Without it, finishing a task that produces a file ends in something like
"the report is at `/workspace/quarterly.xlsx`" — which is useless, because
`/workspace` is inside a runner container the user cannot browse and which is
wiped when it restarts.

## How it works

The agent calls `attach_file` with a path as its own code execution sees it. The
plugin fetches the file from wherever that code ran, stores it under the user's
own directory, and logs an entry the interface renders as a download button.

Retrieval is the awkward part. Agent code runs in a runner — a per-user pod on
its own Linux account on the shared runner — and that shares no volume with the
application. The application image has `ssh` and no `scp` or `sftp`, so the file
comes back through the command channel base64-encoded, using the same endpoint
resolution the code execution tool uses. That is why `max_file_mb` exists: the
content passes through memory twice on the way.

## Who can download what

The endpoint takes an attachment id and nothing else. The owner comes from the
session, so an id copied from another user's transcript is looked up in the
caller's own directory, where it does not exist, and comes back 404. There is no
user id in the request to forget to check.

Files are served `application/octet-stream` with `Content-Disposition:
attachment` and `X-Content-Type-Options: nosniff`. A stored `.html` served as
`text/html` would run in the user's own origin.

## Settings

`default_config.yaml`:

| key | default | what it does |
|---|---|---|
| `max_file_mb` | 25 | Largest file the agent may attach |
| `max_files_per_user` | 200 | Oldest are removed past this; nothing else prunes them |

## Where things live

```
usr/users/<id>/attachments/<attachment-id>/<filename>
usr/users/<id>/attachments/manifest.json
```

Per user, beside their chats and memory, and removed with the account.
