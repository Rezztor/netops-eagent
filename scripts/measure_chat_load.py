"""Measure what booting costs, as a number rather than an impression.

ADR 0002 said the database decision depended on measuring resident memory and
boot time first, and nobody did. This is that measurement.

It exists because the interesting cost is not visible on a developer's instance:
two users and three conversations load in a blink. The cost is O(users x chats),
so the way to see it is to build a corpus at a realistic size and load it.

The corpus is built by *cloning real conversations* rather than by synthesising
plausible-looking ones. A synthetic chat.json would measure how fast Python
parses a file I wrote to be easy to parse; a cloned one measures the real shape,
including the agent history and the log items that make up most of the bytes.

    python scripts/measure_chat_load.py --users 25 --chats-per-user 20

Nothing here touches usr/. The corpus is built in a temporary directory and
removed afterwards.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import string
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def resident_mb() -> float:
    """Current RSS, not the high-water mark.

    ru_maxrss would be easier and would only ever go up, which hides the thing
    we most want to see after adding eviction.
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
    except OSError:
        pass
    return 0.0


def _template_chats(source_root: str) -> list[dict]:
    found = []
    users_root = os.path.join(source_root, "usr", "users")
    for user_id in sorted(os.listdir(users_root)) if os.path.isdir(users_root) else []:
        chats = os.path.join(users_root, user_id, "chats")
        if not os.path.isdir(chats):
            continue
        for ctxid in sorted(os.listdir(chats)):
            path = os.path.join(chats, ctxid, "chat.json")
            if os.path.isfile(path):
                with open(path, encoding="utf-8") as handle:
                    found.append(json.load(handle))
    return found


def _new_id() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=8))


def build_corpus(base: str, templates: list[dict], users: int, per_user: int) -> tuple[int, int]:
    """Insert users x per_user conversations. Returns (chats, bytes).

    Rows now, not files. Inserted as the owner rather than through save_tmp_chat,
    because building a real AgentContext per conversation is the very cost this
    measures.
    """
    from datetime import datetime, timezone

    from psycopg.types.json import Jsonb

    from helpers import db

    chats = written = 0
    with db.unscoped_transaction() as conn:
        conn.execute("TRUNCATE conversations")
        for user_index in range(users):
            user_id = f"{user_index:032x}"
            for chat_index in range(per_user):
                ctxid = _new_id()
                document = dict(templates[chats % len(templates)])
                document["id"] = ctxid
                document["name"] = f"conversation {chat_index} of user {user_index}"
                written += len(json.dumps(document))
                conn.execute(
                    """
                    INSERT INTO conversations (id, owner_id, name, type, created_at,
                                               last_message, document)
                    VALUES (%s, %s, %s, 'user', %s, %s, %s)
                    """,
                    (ctxid, user_id, document["name"], datetime.now(timezone.utc),
                     datetime.now(timezone.utc), Jsonb(document)),
                )
                chats += 1
    return chats, written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=25)
    parser.add_argument("--chats-per-user", type=int, default=20)
    parser.add_argument("--keep", action="store_true", help="leave the corpus on disk")
    args = parser.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    templates = _template_chats(repo)
    if not templates:
        print("No conversations found under usr/users to clone. Have a chat first.")
        return 1

    base = tempfile.mkdtemp(prefix="eagent-measure-")
    try:
        # files._base_dir redirects *everything*, not just usr/ -- prompts, conf
        # and the rest resolve through it too. So the corpus root is a tree of
        # symlinks back to the repository, with only usr/ real. Copying the repo
        # would measure the copy.
        for entry in os.listdir(repo):
            if entry == "usr":
                continue
            os.symlink(os.path.join(repo, entry), os.path.join(base, entry))

        os.makedirs(os.path.join(base, "usr"), exist_ok=True)
        for name in ("settings.json", ".env"):
            source = os.path.join(repo, "usr", name)
            if os.path.exists(source):
                shutil.copy(source, os.path.join(base, "usr", name))

        from helpers import files

        files._base_dir = base

        chats, corpus_bytes = build_corpus(base, templates, args.users, args.chats_per_user)

        from agent import AgentContext
        from helpers import persist_chat

        before = resident_mb()
        started = time.perf_counter()
        persist_chat.load_tmp_chats()
        elapsed = time.perf_counter() - started
        after = resident_mb()

        print()
        print(f"  corpus          {args.users} users x {args.chats_per_user} chats = {chats} conversations")
        print(f"  on disk         {corpus_bytes / 1_048_576:.1f} MB")
        print(f"  loaded          {len(AgentContext._contexts)} contexts resident")
        print(f"  boot blocked    {elapsed:.2f} s")
        print(f"  resident memory {before:.0f} MB -> {after:.0f} MB  (+{after - before:.0f} MB)")
        if chats:
            print(f"  per chat        {elapsed / chats * 1000:.1f} ms, {(after - before) / chats:.2f} MB")
        print()
        return 0
    finally:
        if args.keep:
            print(f"  corpus left at {base}")
        else:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
