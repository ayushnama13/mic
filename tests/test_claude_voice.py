import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_voice import parse_key_spec, KeyMatcher, parse_voice_commands
from transcribe_worker import is_junk_segment

class TestClaudeVoice(unittest.TestCase):
    def test_parse_key_spec(self):
        self.assertEqual(parse_key_spec('F9'), b'\x1b[20~')
        self.assertEqual(parse_key_spec('F10'), b'\x1b[21~')
        self.assertEqual(parse_key_spec('F12'), b'\x1b[24~')
        self.assertEqual(parse_key_spec('ctrl-space'), b'\x00')
        self.assertEqual(parse_key_spec('ctrl+space'), b'\x00')

    def test_key_matcher_full(self):
        matcher = KeyMatcher(b'\x1b[20~')
        matched = False
        rem = b''
        for b in b'\x1b[20~':
            m, r = matcher.feed(b)
            if m:
                matched = True
            if r:
                rem += r
        self.assertTrue(matched)
        self.assertEqual(rem, b'')

    def test_key_matcher_split(self):
        matcher = KeyMatcher(b'\x1b[20~')
        m1, r1 = matcher.feed(b'\x1b'[0])
        self.assertFalse(m1)
        
        matched = False
        for b in b'[20~':
            m, r = matcher.feed(b)
            if m:
                matched = True

        self.assertTrue(matched)

    def test_key_matcher_mismatch(self):
        matcher = KeyMatcher(b'\x1b[20~')
        m1, r1 = matcher.feed(b'\x1b'[0])
        m2, r2 = matcher.feed(b'a'[0])
        self.assertFalse(m1)
        self.assertFalse(m2)
        self.assertEqual(r1 + r2, b'\x1b' + b'a')

    def test_parse_voice_commands_normal(self):
        text, send_enter, keep = parse_voice_commands("hello world new line test")
        self.assertEqual(text, "hello world\ntest")
        self.assertFalse(send_enter)
        self.assertTrue(keep)

    def test_parse_voice_commands_send_it(self):
        text, send_enter, keep = parse_voice_commands("fix this bug send it")
        self.assertEqual(text, "fix this bug")
        self.assertTrue(send_enter)
        self.assertTrue(keep)

    def test_parse_voice_commands_scratch_that(self):
        text, send_enter, keep = parse_voice_commands("ignore previous instructions scratch that")
        self.assertFalse(keep)

    def test_voice_commands_trailing_punctuation(self):
        self.assertEqual(parse_voice_commands("Fix the bug, send it."), ("Fix the bug", True, True))
        self.assertFalse(parse_voice_commands("Never mind. Scratch that!")[2])
        self.assertEqual(parse_voice_commands("a, new line, b")[0], "a\nb")

    def test_is_junk_segment(self):
        self.assertTrue(is_junk_segment("thank you", -0.5, 0.1))
        self.assertTrue(is_junk_segment("hello", -1.5, 0.1))
        self.assertTrue(is_junk_segment("hello", -0.5, 0.8))
        self.assertFalse(is_junk_segment("run pytest please", -0.2, 0.05))

if __name__ == '__main__':
    unittest.main()
