#!/usr/bin/env python3
"""A stand-in for the `tofu` binary: replays a recorded plan, logs every call, and makes nothing.

The test fixture copies this file to `<dir>/tofu`, writes `<dir>/tofu.json` beside it and points
REZZTOR_CLOUD_TOFU at it. Every call (arguments, files in the working directory, the whole environment) is appended
to `<dir>/calls.jsonl`. Config keys: `plan` (the plan JSON `show -json plan.bin` prints), `state` (what `show -json`
prints with no file), `plans` (a list; when present the n-th `plan -out` call replays the n-th and `plan` is not used),
`fail` ({subcommand: message}: that subcommand exits 1 with the message on stderr), `detailed_exitcode` (what `plan -detailed-exitcode` exits with, default 0). Written for the executor's tests; it
supports no subcommand that removes anything, so a call to one is an error here too.
"""
import json
import os
import sys
from pathlib import Path

here = Path(__file__).resolve().parent
cfg = json.loads((here / "tofu.json").read_text())
args = sys.argv[1:]
log_file = here / "calls.jsonl"
earlier = [json.loads(line)["args"] for line in log_file.read_text().splitlines()] if log_file.exists() else []
nth = sum(1 for a in earlier if a[:1] == ["plan"] and any(x.startswith("-out=") for x in a))  # saved plans made so far
with open(log_file, "a") as log:
    log.write(json.dumps({"args": args, "files": sorted(os.listdir(".")), "env": dict(os.environ)}) + "\n")

command = args[0] if args else ""
if command in cfg.get("fail", {}):
    print(cfg["fail"][command], file=sys.stderr)
    sys.exit(1)
if command == "version":
    print(json.dumps({"terraform_version": "1.13.1"}))
elif command in ("init", "validate", "workspace"):
    pass
elif command == "plan":
    if "-detailed-exitcode" in args:
        sys.exit(int(cfg.get("detailed_exitcode", 0)))
    out = next(a.split("=", 1)[1] for a in args if a.startswith("-out="))
    Path(out).write_text(f"ENCRYPTED-PLAN {nth}")
elif command == "show":
    if args[-1] == "plan.bin":
        assert Path("plan.bin").exists(), "show was asked to read a plan that was not made"
        if "plans" in cfg:
            index = int(Path("plan.bin").read_text().split()[1])
            if index >= len(cfg["plans"]):
                print(f"the stand-in has no plan number {index + 1}", file=sys.stderr)
                sys.exit(3)
            print(json.dumps(cfg["plans"][index]))
        else:
            print(json.dumps(cfg["plan"]))
    else:
        print(json.dumps(cfg.get("state", {})))
elif command == "apply":
    assert Path(args[-1]).exists(), "apply needs the saved plan"
else:
    print(f"the stand-in has no subcommand {command!r}", file=sys.stderr)
    sys.exit(2)
