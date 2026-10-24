import unittest
import sys
import os
import json
import time
import shlex
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_voice import parse_key_spec, KeyMatcher, parse_voice_commands, parse_json_response, WorkerManager
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

    def test_parse_json_response(self):
        self.assertEqual(parse_json_response('{"text": "hello"}'), "hello")
        with self.assertRaises(RuntimeError):
            parse_json_response('{"error": "bad"}')
        with self.assertRaises(ValueError):
            parse_json_response('invalid json')

    def test_worker_manager_ready_handshake(self):
        script = "import sys; print('READY', flush=True)\nfor line in sys.stdin: pass"
        cmd = f"{sys.executable} -c {shlex.quote(script)}"
        os.environ['CLAUDE_VOICE_WORKER_CMD'] = cmd
        wm = WorkerManager()
        try:
            wm.start()
            self.assertTrue(wm.wait_ready(timeout=5.0))
        finally:
            wm.shutdown()
            os.environ.pop('CLAUDE_VOICE_WORKER_CMD', None)

    def test_worker_manager_normal_reply(self):
        script = "import sys, json; print('READY', flush=True); line=sys.stdin.readline(); print(json.dumps({'text': 'unit test success'}), flush=True)\nfor line in sys.stdin: pass"
        cmd = f"{sys.executable} -c {shlex.quote(script)}"
        os.environ['CLAUDE_VOICE_WORKER_CMD'] = cmd
        wm = WorkerManager()
        try:
            wm.start()
            res = wm.transcribe("fake.wav")
            self.assertEqual(res, "unit test success")
        finally:
            wm.shutdown()
            os.environ.pop('CLAUDE_VOICE_WORKER_CMD', None)

    def test_worker_manager_error_reply(self):
        script = "import sys, json; print('READY', flush=True); line=sys.stdin.readline(); print(json.dumps({'error': 'transcription failed'}), flush=True)\nfor line in sys.stdin: pass"
        cmd = f"{sys.executable} -c {shlex.quote(script)}"
        os.environ['CLAUDE_VOICE_WORKER_CMD'] = cmd
        wm = WorkerManager()
        try:
            wm.start()
            with self.assertRaises(RuntimeError):
                wm.transcribe("fake.wav")
        finally:
            wm.shutdown()
            os.environ.pop('CLAUDE_VOICE_WORKER_CMD', None)

    def test_worker_manager_worker_dying_mid_request(self):
        fd, state_path = tempfile.mkstemp()
        os.close(fd)
        with open(state_path, "w") as f:
            f.write("0")

        script = f"""
import sys, json, os
print('READY', flush=True)
line = sys.stdin.readline()
path = {repr(state_path)}
with open(path, 'r') as f:
    count = int(f.read().strip())
with open(path, 'w') as f:
    f.write(str(count + 1))
if count == 0:
    sys.exit(1)
else:
    print(json.dumps({{'text': 'recovered text'}}), flush=True)
for line in sys.stdin: pass
"""
        cmd = f"{sys.executable} -c {shlex.quote(script)}"
        os.environ['CLAUDE_VOICE_WORKER_CMD'] = cmd
        wm = WorkerManager()
        try:
            wm.start()
            res = wm.transcribe("fake.wav")
            self.assertEqual(res, "recovered text")
        finally:
            wm.shutdown()
            os.environ.pop('CLAUDE_VOICE_WORKER_CMD', None)
            if os.path.exists(state_path):
                os.remove(state_path)

if __name__ == '__main__':
    unittest.main()
