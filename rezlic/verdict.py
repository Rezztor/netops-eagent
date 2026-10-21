"""What the product concludes: the table "What the product concludes" of spec/formats.md as one pure function, and
the mark of "The clock" as another."""
from __future__ import annotations

from typing import NamedTuple

from .formats import DAY, TOLERANCE


class Verdict(NamedTuple):
    state: str          # licensed, grace1, grace2, restricted
    reason: str         # ok, revoked_notice, ended, no_licence, not_yet_valid, no_lease, released, revoked, trial_ended, grace_over
    end: int | None     # the end time rows 7 to 10 worked from
    clock_behind: bool  # the clock reads more than an hour behind the mark


def verdict(licence: dict | None, lease: dict | None, now: int, mark: int = 0) -> Verdict:
    """`licence` and `lease` are verified payloads (the newest lease, or None). `mark` is the mark of "The clock"
    (see next_mark)."""
    behind = now < mark - TOLERANCE
    if behind:
        now = mark
    if licence is None:
        return Verdict("restricted", "no_licence", None, behind)
    if now < licence["nbf"] - TOLERANCE:
        return Verdict("restricted", "not_yet_valid", None, behind)
    online = not licence["offline"]  # an offline licence has no leases: one offered with it is ignored
    if online and lease is None:
        # the licence document is not secret; only a lease says this installation may run on it
        return Verdict("restricted", "no_lease", None, behind)
    if online and lease["status"] == "released":
        return Verdict("restricted", "released", None, behind)
    revoked = online and lease["status"] == "revoked"
    if revoked and now > lease["notice_until"]:
        return Verdict("restricted", "revoked", None, behind)
    if licence["kind"] == "trial" and now > licence["exp"] + TOLERANCE:
        return Verdict("restricted", "trial_ended", None, behind)
    end = lease["exp"] if online else licence["exp"]
    grace = licence["grace_days"]
    if now <= end + TOLERANCE:
        return Verdict("licensed", "revoked_notice" if revoked else "ok", end, behind)
    if now <= end + min(14, grace) * DAY:
        return Verdict("grace1", "ended", end, behind)
    if now <= end + grace * DAY:
        return Verdict("grace2", "ended", end, behind)
    return Verdict("restricted", "grace_over", end, behind)


def next_mark(mark: int, clock: int, lease_now: int | None = None) -> int:
    """The mark of "The clock" after an event. `clock` is a reading of the product's own clock: it can only raise
    the mark. `lease_now` is the `now` of a lease accepted just now (it answered the nonce just sent, so nothing
    is newer): it sets the mark, downwards too, or one lease signed under a wrong server clock would stop the
    product for good."""
    return max(mark, clock) if lease_now is None else lease_now
