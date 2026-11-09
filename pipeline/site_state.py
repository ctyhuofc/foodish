"""What happens to one restaurant website on a daily run: the per-site lifecycle, in one place.

scrape.py reads every site, then asks `next_step()` what to do with it. The answer names the
outcome (written to data/cache/report.json as "outcome", and summed up by health.py) and the
action to take on the site's saved deals. No network, no files: easy to test (tests/test_site_state.py).

Outcomes                what it means                                         saved deals
  unchanged             same page text as last time, same AI instructions      reused, no AI call
  extracted             new or changed text; the AI read it                     replaced
  no_deals              readable, but nothing that looks like a deal            cleared
  unreachable           couldn't load the site today                            kept (up to STALE_DAYS)
  gone                  unreachable for more than STALE_DAYS                    cleared
  deferred_budget       changed, but today's/this month's AI limit is used up   kept; retried tomorrow
  ai_error              the AI call failed for this site                        kept; retried tomorrow
  deferred_ai_stopped   the AI kept failing the same way, so the run stopped    kept; retried tomorrow
                        calling it for the remaining sites

"Retried tomorrow" works because the new text's fingerprint is only saved once the AI has read it.
"""
from __future__ import annotations

from dataclasses import dataclass

UNCHANGED, EXTRACTED, NO_DEALS = "unchanged", "extracted", "no_deals"
UNREACHABLE, GONE = "unreachable", "gone"
DEFERRED_BUDGET, AI_ERROR, DEFERRED_AI_STOPPED = "deferred_budget", "ai_error", "deferred_ai_stopped"
OUTCOMES = (UNCHANGED, EXTRACTED, NO_DEALS, UNREACHABLE, GONE, DEFERRED_BUDGET, AI_ERROR, DEFERRED_AI_STOPPED)
RETRY_TOMORROW = (DEFERRED_BUDGET, AI_ERROR, DEFERRED_AI_STOPPED)

# Actions on the saved entry
KEEP, REUSE, CLEAR, CALL_AI = "keep", "reuse", "clear", "call_ai"

SAME_ERROR_LIMIT = 3  # stop calling the AI after this many identical errors in a row


@dataclass(frozen=True)
class Step:
    outcome: str | None  # None while the AI call is still to be made (CALL_AI)
    action: str


def next_step(*, readable: bool, fingerprint: str | None, has_deal_words: bool, entry: dict,
              prompt_version: int, stale_before: str, budget_left: bool, ai_stopped: bool) -> Step:
    """Decide what to do with one site. `stale_before` is the ISO date before which a site counts as gone."""
    if not readable:
        if entry and entry.get("ok_at", "9999") < stale_before:
            return Step(GONE, CLEAR)
        return Step(UNREACHABLE, KEEP)
    if entry.get("hash") == fingerprint and entry.get("pv") == prompt_version:
        return Step(UNCHANGED, REUSE)
    if not has_deal_words:
        return Step(NO_DEALS, CLEAR)
    if ai_stopped:
        return Step(DEFERRED_AI_STOPPED, KEEP)
    if not budget_left:
        return Step(DEFERRED_BUDGET, KEEP)
    return Step(None, CALL_AI)


class AIBreaker:
    """Stops AI calls for the rest of the run once the last SAME_ERROR_LIMIT AI errors were identical
    (e.g. "credit balance too low" three times): more calls would only fail the same way."""

    def __init__(self, limit: int = SAME_ERROR_LIMIT):
        self.limit, self.errors, self.stopped_with = limit, [], None

    @property
    def stopped(self) -> bool:
        return self.stopped_with is not None

    def failure(self, message: str) -> None:
        self.errors.append(message)
        tail = self.errors[-self.limit:]
        if len(tail) == self.limit and len(set(tail)) == 1:
            self.stopped_with = message
