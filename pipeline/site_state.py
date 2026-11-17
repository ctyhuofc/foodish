"""What happens to one restaurant website on a daily run: the per-site lifecycle, in one place.

scrape.py reads every site, then asks `next_step()` what to do with it. The answer names the
outcome (written to data/cache/report.json as "outcome", and summed up by health.py) and the
action to take on the site's saved deals. No network, no files: easy to test (tests/test_site_state.py).

Outcomes                what it means                                         saved deals
  unchanged             same page text as last time, same AI instructions      reused, no AI call
  extracted             new or changed text; the AI read it                     replaced
  no_deals              readable, but nothing that looks like a deal            cleared
  blocked               its robots.txt blocks FoodishBot, or the restaurant     cleared
                        asked to be removed (data/removed.json)
  unreachable           couldn't load the site today                            kept (up to STALE_DAYS)
  gone                  unreachable for more than STALE_DAYS                    cleared
  deferred_budget       changed, but today's/this month's AI limit is used up   kept; retried tomorrow
  ai_error              the AI call failed for this site                        kept; retried tomorrow
  deferred_ai_stopped   the AI kept failing the same way, so the run stopped    kept; retried tomorrow
                        calling it for the remaining sites
  deferred_ai_backoff   the AI couldn't make sense of this same text before,    kept; retried after a pause
                        so it isn't asked again until the pause is over         (or at once if the text changes)

"Retried tomorrow" works because the new text's fingerprint is only saved once the AI has read it.
A site whose text keeps getting an unreadable AI reply would be retried (and paid for) every day, forever;
`ai_failed()` notes it on the site's saved entry ("ai_fail") and `next_step()` waits BACKOFF_DAYS first.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

UNCHANGED, EXTRACTED, NO_DEALS = "unchanged", "extracted", "no_deals"
UNREACHABLE, GONE, BLOCKED = "unreachable", "gone", "blocked"
DEFERRED_BUDGET, AI_ERROR, DEFERRED_AI_STOPPED = "deferred_budget", "ai_error", "deferred_ai_stopped"
DEFERRED_AI_BACKOFF = "deferred_ai_backoff"
OUTCOMES = (UNCHANGED, EXTRACTED, NO_DEALS, UNREACHABLE, GONE, BLOCKED, DEFERRED_BUDGET, AI_ERROR, DEFERRED_AI_STOPPED,
            DEFERRED_AI_BACKOFF)
RETRY_TOMORROW = (DEFERRED_BUDGET, AI_ERROR, DEFERRED_AI_STOPPED)

# After an unreadable AI reply, wait this many days before asking about the same text again:
# 3 after the first failure, 14 once it has failed more than once in a row.
BACKOFF_DAYS = (3, 14)

# Actions on the saved entry
KEEP, REUSE, CLEAR, CALL_AI = "keep", "reuse", "clear", "call_ai"

SAME_ERROR_LIMIT = 3  # stop calling the AI after this many identical errors in a row

# Whether a site could be read today ("status" in report.json, before the outcome above is decided).
# Other statuses are free-text errors ("error 404", "timeout", ...). These values are in old reports: don't change them.
READ_OK = "ok"
READ_REMOVED = "removed by request"        # listed in data/removed.json
READ_ROBOTS = "blocked by robots.txt"
BLOCKED_READS = (READ_REMOVED, READ_ROBOTS)  # -> outcome BLOCKED, deals cleared at once


@dataclass(frozen=True)
class Step:
    outcome: str | None  # None while the AI call is still to be made (CALL_AI)
    action: str


def next_step(*, readable: bool, fingerprint: str | None, has_deal_words: bool, entry: dict,
              prompt_version: int, stale_before: str, budget_left: bool, ai_stopped: bool,
              blocked: bool = False, today: str = "") -> Step:
    """Decide what to do with one site. `stale_before` is the ISO date before which a site counts as gone.
    `blocked`: the site said no (robots.txt) or asked to be removed; its deals go at once, not after STALE_DAYS.
    `today` (ISO date) is only needed for the pause after an unreadable AI reply (see ai_failed)."""
    if blocked:
        return Step(BLOCKED, CLEAR)
    if not readable:
        if entry and entry.get("ok_at", "9999") < stale_before:
            return Step(GONE, CLEAR)
        return Step(UNREACHABLE, KEEP)
    if entry.get("hash") == fingerprint and entry.get("pv") == prompt_version:
        return Step(UNCHANGED, REUSE)
    if not has_deal_words:
        return Step(NO_DEALS, CLEAR)
    fail = entry.get("ai_fail") or {}
    if fail.get("hash") == fingerprint and fail.get("pv") == prompt_version and today and today < str(fail.get("until", "")):
        return Step(DEFERRED_AI_BACKOFF, KEEP)  # same text the AI couldn't read: wait until "until"
    if ai_stopped:
        return Step(DEFERRED_AI_STOPPED, KEEP)
    if not budget_left:
        return Step(DEFERRED_BUDGET, KEEP)
    return Step(None, CALL_AI)


def ai_failed(previous: dict | None, fingerprint: str, prompt_version: int, today: str) -> dict:
    """The "ai_fail" note to save on a site's entry after an unreadable AI reply: which text (fingerprint and
    AI instructions), when, how many times in a row, and the first day to try again. Only for unreadable
    replies: an AI outage or a credit problem isn't this site's fault and is retried tomorrow as before."""
    count = (previous or {}).get("count", 0) + 1
    wait = BACKOFF_DAYS[0] if count == 1 else BACKOFF_DAYS[1]
    until = (dt.date.fromisoformat(today) + dt.timedelta(days=wait)).isoformat()
    return {"hash": fingerprint, "pv": prompt_version, "at": today, "count": count, "until": until}


def error_kind(message: str) -> str:
    """An AI error without the part that changes from site to site: "unreadable AI reply: 'Sorry, I…'"
    becomes "unreadable AI reply", so three of them in a row are seen as the same problem."""
    return re.sub(r"^(unreadable AI reply):.*", r"\1", str(message), flags=re.S)


class AIBreaker:
    """Stops AI calls for the rest of the run once the last SAME_ERROR_LIMIT AI calls in a row all failed
    the same way (e.g. "credit balance too low" three times): more calls would only fail the same way.
    "The same way" ignores the quoted reply in "unreadable AI reply: ..." (error_kind). A call that works
    starts the count again, so a few odd websites spread through a run can't stop the AI for everyone:
    only three different sites in a row (each already asked twice) getting unreadable replies can, and
    that means the AI itself is misbehaving."""

    def __init__(self, limit: int = SAME_ERROR_LIMIT):
        self.limit, self.errors, self.stopped_with = limit, [], None

    @property
    def stopped(self) -> bool:
        return self.stopped_with is not None

    def failure(self, message: str) -> None:
        self.errors.append(error_kind(message))
        tail = self.errors[-self.limit:]
        if len(tail) == self.limit and len(set(tail)) == 1:
            self.stopped_with = message  # the full message: health.py reads it for the fix to suggest

    def success(self) -> None:
        self.errors.clear()
