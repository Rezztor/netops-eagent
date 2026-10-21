"""Measure what the reviewer decides, against cases with a known right answer.

Not a pytest test: it calls the configured utility model, so it costs tokens, is
non-deterministic, and cannot run in CI. Run it by hand after changing
prompts/auto_skills.review.sys.md.

    python plugins/_ea_auto_skills/evals/run_eval.py 4

Read the per-case column, not the total. A single pass is noise -- the same
prompt has scored 5/7 and 6/7 on consecutive runs -- so each case is repeated and
what matters is how often it lands where it should. A case that splits 2/4 is
telling you the answer is genuinely arguable, not that the prompt is broken.

Recorded measurements, four repeats each, at the time of writing:

    prompt variant                overall   on the five unambiguous cases
    shipped                        23/28    20/20
    procedure-only (previous)      24/28    17/20
    procedure-only + constraints   22/28    18/20

The shipped prompt is not the best on the total. It is the best on the cases
whose expected answer can be checked against something, and it loses only on the
two where reasonable people disagree. Prefer that reading; the total flatters
whichever variant happens to be conservative on the arguable cases.
"""

import asyncio
import collections
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import initialize  # noqa: E402
from agent import Agent  # noqa: E402
from cases import CASES, PROPOSE, SKIP  # noqa: E402
from plugins._ea_auto_skills.helpers import review  # noqa: E402
from plugins._ea_auto_skills.helpers.capture import Candidate  # noqa: E402


async def main(repeats: int, user_id: str) -> None:
    agent = Agent(0, initialize.initialize_agent())
    outcomes: dict[str, list[str]] = collections.defaultdict(list)

    for _ in range(repeats):
        for case in CASES:
            candidate = Candidate(
                sequence=1,
                tool_calls=3,
                evidence=case["evidence"],
                loaded_skills=case["loaded"],
                context_id=case["id"],
            )
            try:
                draft = await review._ask(
                    agent, candidate, {"review_timeout_seconds": 120}, user_id
                )
            except Exception as exception:
                outcomes[case["id"]].append(f"error: {type(exception).__name__}")
                continue
            decided = SKIP if (draft is None or draft.action == SKIP) else PROPOSE
            outcomes[case["id"]].append(decided)

    print(f"\n{'case':26} {'expected':9} {'agreed':>7}  outcomes")
    agreed = 0
    for case in CASES:
        got = outcomes[case["id"]]
        hits = sum(one == case["expect"] for one in got)
        agreed += hits
        print(
            f"{case['id']:26} {case['expect']:9} {hits}/{len(got):<5}  "
            f"{dict(collections.Counter(got))}"
        )
    print(f"\nagreement {agreed}/{len(CASES) * repeats}")


if __name__ == "__main__":
    repeats = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    # Any id will do; it only decides which skills are marked [yours].
    owner = sys.argv[2] if len(sys.argv) > 2 else "eval"
    asyncio.run(main(repeats, owner))
