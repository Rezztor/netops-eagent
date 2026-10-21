"""Review, then copy, the allow-listed Markdown of Microsoft's azure-skills into the Azure plugin; writes VENDOR.json.

Developer-time only, called by scripts/sync_azure_skills.sh; the product never runs it. Stdlib only.

`review` writes nothing into the plugin. It writes a report (stdout and <report-dir>/azure-skills-review-<commit>.md): every
file added, removed and changed since the vendored commit with its diff, and a flag for each new line a reviewer must read.
`accept` copies only after a report exists for that same commit and the same staged content, and records who reviewed and when.
It copies only `*.md` regular files, renames every SKILL.md to OVERVIEW.md (so the skill loader never registers one of
Microsoft's skills as ours), writes the LICENSE and VENDOR.json. Output is deterministic: one commit twice, same bytes.
"""
import argparse
import calendar
import difflib
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from datetime import date
from pathlib import Path

# What a reviewer must read before a new line of Microsoft's text is imported. A line already vendored is not flagged again.
FLAGS = {
    "tells the agent what to do or hides it": r"ignore (all |any |the )?(previous|prior|above)|disregard|do not (tell|inform|mention|reveal)|don't (tell|mention)|without (telling|informing)|system prompt|<\s*system|you are now|exfiltrat|mandatory|authoritative|follow (these|this|them) (instructions )?exactly",
    "deletes, stops, forces or changes access": r"\b(az|kubectl|helm|terraform|tofu)\b[^\n]*\b(delete|destroy|stop|deallocate|restart|purge|uninstall)\b|rm\s+-rf|--force|role assignment|az role\b|az ad\b",
    "downloads and runs code": r"(curl|wget)[^\n]*\|\s*(sudo\s+)?(ba|z)?sh|Invoke-WebRequest|\biwr\b|\birm\b|\biex\b|npx\s|pip3? install|npm (i|install)\b|bash\s+<\(|chmod \+x",
    "touches a credential": r"client.?secret|password|passwd|AccountKey=|SharedAccessSignature|-----BEGIN|az login|create-for-rbac|get-access-token|api[_-]?key",
    "hides text": r"<!--|[​-‏‪-‮⁠-⁤﻿\U000e0000-\U000e007f]",
}
OK_HOSTS = (".microsoft.com", "microsoft.com", ".azure.com", ".windows.net", ".microsoftonline.com", ".azure.net", ".azurewebsites.net",
            ".azurecontainerapps.io", "aka.ms", "github.com", ".githubusercontent.com", "localhost")
URL = re.compile(r"https?://([A-Za-z0-9.-]+)")
LINK_FLAG = "links outside Microsoft docs and GitHub"


def stage(clone: Path, skills: list[str], url: str, sha: str):
    staged, skipped = {}, Counter()
    for skill in skills:
        root = clone / "skills" / skill
        if not root.is_dir():
            sys.exit(f"sync_azure_skills: allow-listed skill {skill!r} is not in {url} at {sha}")
        for path in sorted(root.rglob("*")):
            if path.is_dir():
                continue
            if path.suffix != ".md" or path.is_symlink() or not path.is_file():
                skipped[path.suffix or path.name] += 1  # a link could point outside the clone; only regular .md is copied
                continue
            rel = path.relative_to(clone / "skills")
            if path.name == "SKILL.md":
                rel = rel.with_name("OVERVIEW.md")
            if rel.as_posix() in staged:
                sys.exit(f"sync_azure_skills: {rel} would be written twice")
            # .gitattributes normalises line endings to LF on commit; ship LF so the recorded hash survives a checkout
            staged[rel.as_posix()] = path.read_bytes().replace(b"\r\n", b"\n")
    upstream = sorted(p.name for p in (clone / "skills").iterdir() if p.is_dir())
    return staged, skipped, upstream


def digest(staged: dict, skills: list[str]) -> str:
    h = hashlib.sha256(" ".join(skills).encode())
    for rel, data in sorted(staged.items()):
        h.update(f"\n{rel} {hashlib.sha256(data).hexdigest()}".encode())
    return h.hexdigest()


def text(data: bytes) -> list[str]:
    return data.decode("utf-8", "replace").splitlines()


def vendored(dest: Path) -> dict:
    try:
        v = json.loads((dest / "VENDOR.json").read_text())
        v["_bytes"] = {rel: (dest / rel).read_bytes() for rel in v["files"]}
        return v
    except (OSError, ValueError, KeyError):
        return {"commit": None, "_bytes": {}, "upstream_skills": [], "not_copied": {}}


def review(staged, skipped, upstream, dest: Path, sha: str, commit_date: str, skills: list[str]) -> tuple[str, str]:
    """Return (the part worth reading on a terminal, the same plus every diff)."""
    old = vendored(dest)
    olds = old["_bytes"]
    added, removed = sorted(set(staged) - set(olds)), sorted(set(olds) - set(staged))
    changed = sorted(r for r in staged if r in olds and staged[r] != olds[r])
    out = [f"staged-digest: {digest(staged, skills)}",
           f"review of {sha} ({commit_date}) against {old['commit'] or 'nothing vendored'}",
           f"files: {len(added)} added, {len(removed)} removed, {len(changed)} changed, "
           f"{len(staged) - len(added) - len(changed)} unchanged"]
    for label, names in (("added", added), ("removed", removed), ("changed", changed)):
        out += [f"  {label}: {n}" for n in names]
    new_skills = sorted(set(upstream) - set(skills) - set(old["upstream_skills"]))
    new_types = sorted(k for k in skipped if k not in old["not_copied"])
    out.append("not copied (not Markdown): " + (", ".join(f"{n} x {k}" for k, n in sorted(skipped.items())) or "none"))
    out.append("NEW file types in the allow-listed skills (scripts, configs, binaries): " + (", ".join(new_types) or "none"))
    out.append("NEW skills upstream (not allow-listed, not seen at the last review): " + (", ".join(new_skills) or "none"))
    seen = {(r, ln.strip()) for r, data in olds.items() for ln in text(data)}
    counts, lines = Counter(), []
    for rel in added + changed:
        for no, line in enumerate(text(staged[rel]), 1):
            if (rel, line.strip()) in seen:
                continue
            hits = [label for label, pattern in FLAGS.items() if re.search(pattern, line, re.I)]
            if any(not h.endswith(OK_HOSTS) for h in URL.findall(line)):
                hits.append(LINK_FLAG)
            for label in hits:
                counts[label] += 1
                lines.append(f"  [{label}] {rel}:{no}: {line.strip()[:200]}")
    out.append("flags on lines new since the vendored copy: " + (", ".join(f"{k}: {n}" for k, n in sorted(counts.items())) or "none"))
    out += lines
    diffs = []
    for rel in added + removed + changed:
        a, b = (text(olds[rel]) if rel in olds else []), (text(staged[rel]) if rel in staged else [])
        diffs += ["", f"## {rel}", "```diff", *difflib.unified_diff(a, b, f"vendored/{rel}", f"upstream/{rel}", lineterm=""), "```"]
    summary = "\n".join(out)
    return summary, summary + "\n\n# Diffs\n" + "\n".join(diffs) + "\n"


def plus_month(d: date) -> date:
    y, m = d.year + d.month // 12, d.month % 12 + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def write(staged, skipped, upstream, dest, sha, commit_date, url, skills, clone, reviewer) -> None:
    today = date.today()
    shutil.rmtree(dest, ignore_errors=True)  # a file Microsoft dropped must not stay behind
    for rel, data in staged.items():
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        (dest / rel).write_bytes(data)
    (dest / "LICENSE").write_bytes((clone / "LICENSE").read_bytes().replace(b"\r\n", b"\n"))
    manifest = {"source": url, "commit": sha, "commit_date": commit_date, "license": "MIT", "allow_list": skills,
                "upstream_skills": upstream, "not_copied": dict(sorted(skipped.items())),
                "reviewed_by": reviewer, "reviewed_on": today.isoformat(), "next_review_due": plus_month(today).isoformat(),
                "files": {rel: hashlib.sha256(data).hexdigest() for rel, data in sorted(staged.items())}}
    (dest / "VENDOR.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=("review", "accept"))
    ap.add_argument("clone", type=Path)
    ap.add_argument("dest", type=Path)
    ap.add_argument("report_dir", type=Path)
    ap.add_argument("sha")
    ap.add_argument("commit_date")
    ap.add_argument("url")
    ap.add_argument("--reviewer", default="")
    ap.add_argument("skills", nargs="+")
    a = ap.parse_args()
    staged, skipped, upstream = stage(a.clone, a.skills, a.url, a.sha)
    report = a.report_dir / f"azure-skills-review-{a.sha}.md"
    if a.mode == "review":
        summary, full = review(staged, skipped, upstream, a.dest, a.sha, a.commit_date, a.skills)
        a.report_dir.mkdir(parents=True, exist_ok=True)
        report.write_text(full, encoding="utf-8")
        print(summary)
        print(f"\nfull report with every diff: {report}\nnothing imported; read it, then run again with --accept --reviewer NAME")
        return
    if not a.reviewer.strip():
        sys.exit("sync_azure_skills: --accept needs --reviewer NAME, the person who read the report")
    if not report.is_file() or f"staged-digest: {digest(staged, a.skills)}" != report.read_text().split("\n", 1)[0]:
        sys.exit(f"sync_azure_skills: no review report for this exact content of {a.sha}; run the review first ({report})")
    old = vendored(a.dest)
    if old["commit"] == a.sha and {r: hashlib.sha256(d).hexdigest() for r, d in staged.items()} == json.loads(
            (a.dest / "VENDOR.json").read_text())["files"]:
        print(f"already vendored: {a.sha}, nothing changed")
        return
    write(staged, skipped, upstream, a.dest, a.sha, a.commit_date, a.url, a.skills, a.clone, a.reviewer.strip())
    print(f"accepted: {len(staged)} Markdown files written to {a.dest}, reviewed by {a.reviewer.strip()}")


if __name__ == "__main__":
    main()
