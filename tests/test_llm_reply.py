"""An AI reply we can't read must be an error (deals kept, retried), never "no deals" (pipeline/llm.py)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
import llm  # noqa: E402


class ParseReply(unittest.TestCase):
    def test_real_empty_answer_is_no_deals(self):
        self.assertEqual(llm.parse_reply('{"deals": []}'), [])

    def test_deals_are_read_even_with_text_around_them(self):
        self.assertEqual(llm.parse_reply('Here you go:\n{"deals": [{"title": "Wing night"}]}'), [{"title": "Wing night"}])

    def test_unreadable_replies_are_errors(self):
        for out in ["", "Sorry, I can't help", '{"deals": [{"title": "Wing', '{"result": []}', '{"deals": "none"}', None]:
            with self.assertRaises(llm.LLMError, msg=repr(out)):
                llm.parse_reply(out)


class ExtractDeals(unittest.TestCase):
    def run_with(self, replies):
        calls = iter(replies)
        def fake(prompt):
            r = next(calls)
            if isinstance(r, Exception):
                raise r
            return r
        with mock.patch.dict("os.environ", {"LLM_PROVIDER": "anthropic"}), \
             mock.patch.object(llm, "_call_anthropic", fake), mock.patch.object(llm.time, "sleep"):
            return llm.extract_deals("A", "https://a.ca", "text")

    def test_one_unreadable_reply_is_retried(self):
        self.assertEqual(self.run_with(["cut off {", '{"deals": []}']), [])

    def test_two_unreadable_replies_raise(self):
        with self.assertRaises(llm.LLMError):
            self.run_with(["cut off {", "still cut off {"])

    def test_busy_three_times_raises_instead_of_returning_no_deals(self):
        busy = llm.LLMError("HTTP 529 overloaded")
        busy.status = 529
        with self.assertRaises(llm.LLMError):
            self.run_with([busy, busy, busy])
