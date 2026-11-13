"""The per-site lifecycle (pipeline/site_state.py): every outcome, and the AI circuit breaker."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import site_state as ss  # noqa: E402

BASE = dict(readable=True, fingerprint="new", has_deal_words=True, prompt_version=4,
            stale_before="2026-10-26", budget_left=True, ai_stopped=False)
SEEN = {"hash": "old", "pv": 4, "ok_at": "2026-11-08", "deals": [{"title": "Wings"}]}


def step(entry=SEEN, **kw):
    return ss.next_step(**{**BASE, "entry": entry, **kw})


class NextStep(unittest.TestCase):
    def test_new_site_with_deal_words_calls_the_ai(self):
        self.assertEqual(step(entry={}), ss.Step(None, ss.CALL_AI))

    def test_same_text_same_instructions_is_reused(self):
        self.assertEqual(step(fingerprint="old"), ss.Step(ss.UNCHANGED, ss.REUSE))

    def test_same_text_but_new_ai_instructions_is_read_again(self):
        self.assertEqual(step(fingerprint="old", prompt_version=5).action, ss.CALL_AI)

    def test_no_deal_words_clears_without_ai(self):
        self.assertEqual(step(has_deal_words=False), ss.Step(ss.NO_DEALS, ss.CLEAR))

    def test_out_of_budget_keeps_old_deals(self):
        self.assertEqual(step(budget_left=False), ss.Step(ss.DEFERRED_BUDGET, ss.KEEP))

    def test_after_ai_stopped_keeps_old_deals(self):
        self.assertEqual(step(ai_stopped=True), ss.Step(ss.DEFERRED_AI_STOPPED, ss.KEEP))

    def test_unchanged_sites_still_count_after_ai_stopped(self):
        self.assertEqual(step(fingerprint="old", ai_stopped=True).outcome, ss.UNCHANGED)

    def test_blocked_or_removed_site_loses_its_deals_at_once(self):
        self.assertEqual(step(readable=False, blocked=True), ss.Step(ss.BLOCKED, ss.CLEAR))
        self.assertEqual(step(blocked=True).outcome, ss.BLOCKED)  # even if some text came back
        self.assertIn(ss.BLOCKED, ss.OUTCOMES)

    def test_unreachable_recently_keeps_deals(self):
        self.assertEqual(step(readable=False, fingerprint=None), ss.Step(ss.UNREACHABLE, ss.KEEP))

    def test_unreachable_too_long_is_gone(self):
        old = {**SEEN, "ok_at": "2026-10-01"}
        self.assertEqual(step(entry=old, readable=False, fingerprint=None), ss.Step(ss.GONE, ss.CLEAR))

    def test_never_seen_and_unreachable_is_just_unreachable(self):
        self.assertEqual(step(entry={}, readable=False, fingerprint=None).outcome, ss.UNREACHABLE)

    def test_every_outcome_is_reachable(self):
        seen = {step(fingerprint="old").outcome, step(has_deal_words=False).outcome, step(budget_left=False).outcome,
                step(ai_stopped=True).outcome, step(readable=False, fingerprint=None).outcome,
                step(entry={**SEEN, "ok_at": "2026-01-01"}, readable=False, fingerprint=None).outcome,
                step(readable=False, fingerprint=None, blocked=True).outcome}
        seen |= {ss.EXTRACTED, ss.AI_ERROR}  # set by scrape.py after the AI call
        self.assertEqual(seen, set(ss.OUTCOMES))


class Breaker(unittest.TestCase):
    def test_three_identical_errors_stop_it(self):
        b = ss.AIBreaker()
        b.failure("credit balance too low"); b.failure("credit balance too low")
        self.assertFalse(b.stopped)
        b.failure("credit balance too low")
        self.assertEqual(b.stopped_with, "credit balance too low")

    def test_different_errors_do_not(self):
        b = ss.AIBreaker()
        for msg in ("timeout", "overloaded", "timeout"):
            b.failure(msg)
        self.assertFalse(b.stopped)


if __name__ == "__main__":
    unittest.main()
