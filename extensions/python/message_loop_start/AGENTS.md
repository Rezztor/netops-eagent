# Message Loop Start Extensions DOX

## Purpose

- Own backend behavior that runs at the start of each message loop iteration.

## Ownership

- Ordered Python files own iteration counters and future loop-start state setup.
- `_20_iteration_budget.py` owns the per-run step budget: at `loop_data.iteration == MAX_ITERATIONS` (100; `REZZTOR_MAX_LOOP_ITERATIONS` overrides it, `0` turns it off) it adds a warning to history and the chat log telling the model to give its final response; two iterations later it logs and raises `HandledException`, which ends the run as the unusable-response limit does. It writes the warning itself instead of raising `RepairableException`: this hook runs outside the message loop's `try`, where a raise restarts the run with a fresh `LoopData` and so a fresh count (audit 6 M3).

## Local Contracts

- Keep per-loop counters deterministic and scoped to the active context.
- Do not reset state owned by monologue-level hooks.
- `loop_data.iteration` counts from 0 in each run (a new `LoopData` per monologue); `_10_iteration_no.py`'s `iteration_no` is the agent's lifetime total and is not a budget.

## Work Guidance

- Coordinate loop-start state changes with logging, streaming, and process-chain behavior.

## Verification

- Smoke-test multi-turn conversations after changes.

## Child DOX Index

No child DOX files.
