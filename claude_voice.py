#!/usr/bin/env python3
import os
import sys
import pty
import tty
import termios
import select
import signal
import fcntl
import subprocess
import threading
import time
import tempfile
import wave
import re
import json
import shlex

def parse_key_spec(spec):
    if not spec:
        return b'\x1b[20~' # F9 default
    spec_lower = spec.lower().strip()
    if spec_lower == 'f9':
        return b'\x1b[20~'
    elif spec_lower == 'f10':
        return b'\x1b[21~'
    elif spec_lower == 'f12':
        return b'\x1b[24~'
    elif spec_lower in ('ctrl-space', 'ctrl+space'):
        return b'\x00'
    try:
        return spec.encode().decode('unicode_escape').encode('latin1')
    except Exception:
        return spec.encode('latin1', errors='ignore')

class KeyMatcher:
    def __init__(self, target_key):
        self.target = target_key
        self.buf = bytearray()

    def feed(self, b_val):
        self.buf.append(b_val)
        if bytes(self.buf) == self.target:
            self.buf.clear()
            return True, b''
        elif self.target.startswith(bytes(self.buf)):
            return False, b''
        else:
            rem = bytes(self.buf)
            self.buf.clear()
            return False, rem

_TRAIL = r"[\s.,!?;:]*$"

def parse_voice_commands(text):
    if not text:
        return "", False, False

    text_clean = text.strip()
    keep = True
    send_enter = False

    m = re.search(r"\bscratch\s+that" + _TRAIL, text_clean, re.IGNORECASE)
    if m:
        return "", False, False

    m = re.search(r"[\s.,!?;:]*\bsend\s+it" + _TRAIL, text_clean, re.IGNORECASE)
    if m:
        send_enter = True
        text_clean = text_clean[:m.start()].strip()

    text_clean = re.sub(r"[\s,.]*\bnew\s+line\b[\s,.]*", "\n", text_clean, flags=re.IGNORECASE)

    return text_clean, send_enter, keep

def log_error(msg):
    try:
        cache_dir = os.path.expanduser("~/.cache/claude-voice")
        os.makedirs(cache_dir, exist_ok=True)
        log_path = os.path.join(cache_dir, "log.txt")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {msg}\n")
    except Exception:
        pass

class Recorder:
    def __init__(self):
        self.proc = None
        self.pcm_data = bytearray()
        self.thread = None
        self.recording = False
        self.start_time = 0

    def start(self):
        self.pcm_data.clear()
        self.recording = True
        self.start_time = time.time()
        cmd = ['parecord', '--raw', '--format=s16le', '--rate=16000', '--channels=1', '--latency-msec=20']
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception as e:
            raise RuntimeError(f"parecord not found or failed to start: {e}")

        def read_loop():
            while self.recording and self.proc and self.proc.stdout:
                chunk = self.proc.stdout.read(4096)
                if not chunk:
                    break
                self.pcm_data.extend(chunk)

        self.thread = threading.Thread(target=read_loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.recording = False
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        if self.thread:
            self.thread.join(timeout=1.0)
        return bytes(self.pcm_data)

def set_title(title):
    try:
        sys.stdout.write(f"\x1b]0;{title}\x07")
        sys.stdout.flush()
    except Exception:
        pass

def ring_bell():
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except Exception:
        pass

def parse_json_response(line):
    try:
        data = json.loads(line.strip())
    except Exception as e:
        raise ValueError(f"Invalid JSON response: {line} ({e})")
    if "error" in data:
        raise RuntimeError(f"Worker error: {data['error']}")
    if "text" in data:
        return data["text"]
    raise ValueError(f"Unknown response format: {data}")

class WorkerManager:
    def __init__(self):
        self.proc = None
        self.ready_event = threading.Event()
        self.lock = threading.Lock()
        self.stderr_file = None
        self.closing = False

    def start(self):
        with self.lock:
            self._start_unlocked()

    def _start_unlocked(self):
        if self.closing:
            raise RuntimeError("worker is shutting down")
        if self.proc and self.proc.poll() is None:
            return
        self.ready_event.clear()
        set_title("[loading model] claude")
        cmd_env = os.environ.get('CLAUDE_VOICE_WORKER_CMD')
        if cmd_env:
            cmd = shlex.split(cmd_env)
        else:
            python_exec = "/home/ayush/vscoder/Documents/vscode/transcriber/.venv/bin/python"
            worker_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcribe_worker.py")
            cmd = [python_exec, worker_script, '--serve']

        cache_dir = os.path.expanduser("~/.cache/claude-voice")
        os.makedirs(cache_dir, exist_ok=True)
        log_path = os.path.join(cache_dir, "worker.log")
        try:
            self.stderr_file = open(log_path, "a", encoding="utf-8")
        except Exception:
            self.stderr_file = subprocess.DEVNULL

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.stderr_file,
                text=True
            )
        except Exception as e:
            log_error(f"Failed to start worker: {e}")
            set_title("claude")
            raise

        def reader_thread():
            try:
                while self.proc and self.proc.stdout:
                    line = self.proc.stdout.readline()
                    if not line:
                        break
                    stripped = line.strip()
                    if stripped == "READY":
                        self.ready_event.set()
                        set_title("claude")
                        break
                    else:
                        log_error(f"Worker stdout: {stripped}")
            except Exception as e:
                log_error(f"Worker reader error: {e}")
            finally:
                set_title("claude")

        threading.Thread(target=reader_thread, daemon=True).start()

    def wait_ready(self, timeout=180.0):
        return self.ready_event.wait(timeout=timeout)

    def shutdown(self):
        # no lock: an in-flight transcribe() holds it for up to minutes; closing the
        # worker's stdin makes it exit and unblocks that call
        self.closing = True
        self._shutdown_unlocked()

    def _shutdown_unlocked(self):
        if not self.proc:
            return
        try:
            if self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except Exception:
                    pass
            self.proc.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                self.proc.kill()
                self.proc.wait(timeout=1.0)
            except Exception:
                pass
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        self.proc = None
        self.ready_event.clear()
        if self.stderr_file and not isinstance(self.stderr_file, int):
            try:
                self.stderr_file.close()
            except Exception:
                pass
        self.stderr_file = None

    def transcribe(self, wav_path):
        with self.lock:
            if not self.proc or self.proc.poll() is not None:
                self._start_unlocked()

            if not self.ready_event.wait(timeout=180.0):
                raise RuntimeError("Worker failed to become ready (timeout)")

            try:
                self.proc.stdin.write(wav_path + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
                if not line:
                    raise EOFError("Worker closed stdout")
                data = json.loads(line.strip())
            except Exception as e:
                log_error(f"Worker communication error: {e}. Respawning worker.")
                self._shutdown_unlocked()
                self._start_unlocked()
                if not self.ready_event.wait(timeout=180.0):
                    raise RuntimeError("Worker failed to become ready after respawn")
                try:
                    self.proc.stdin.write(wav_path + "\n")
                    self.proc.stdin.flush()
                    line = self.proc.stdout.readline()
                    if not line:
                        raise EOFError("Worker closed stdout after respawn")
                    data = json.loads(line.strip())
                except Exception as ex:
                    raise RuntimeError(f"Worker failed after respawn: {ex}")

            if "error" in data:
                raise RuntimeError(f"Worker error: {data['error']}")
            if "text" in data:
                return data["text"]
            raise ValueError(f"Unknown response format: {data}")

def main():
    key_spec = os.environ.get('CLAUDE_VOICE_KEY', 'F9')
    target_key = parse_key_spec(key_spec)
    max_secs = float(os.environ.get('CLAUDE_VOICE_MAX_SECS', '120'))

    cmd_env = os.environ.get('CLAUDE_VOICE_CMD')
    if cmd_env:
        child_cmd = shlex.split(cmd_env)
    else:
        child_cmd = ['claude'] + sys.argv[1:]

    pid, pty_fd = pty.fork()
    if pid == 0:
        # Child process
        try:
            os.execvp(child_cmd[0], child_cmd)
        except Exception as e:
            print(f"Failed to execute {child_cmd[0]}: {e}", file=sys.stderr)
            sys.exit(1)

    # Parent process
    has_raw = False
    old_term = None
    if sys.stdin.isatty():
        try:
            old_term = termios.tcgetattr(sys.stdin.fileno())
            tty.setraw(sys.stdin.fileno())
            has_raw = True
        except Exception:
            pass

    def handle_sigwinch(signum, frame):
        try:
            import struct
            s = fcntl.ioctl(sys.stdin.fileno(), termios.TIOCGWINSZ, b'\x00' * 8)
            fcntl.ioctl(pty_fd, termios.TIOCSWINSZ, s)
        except Exception:
            pass

    signal.signal(signal.SIGWINCH, handle_sigwinch)
    handle_sigwinch(None, None)

    worker_manager = WorkerManager()
    worker_manager.start()

    def handle_shutdown(signum, frame):
        if worker_manager:
            worker_manager.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGHUP, handle_shutdown)

    recorder = Recorder()
    recording = False
    matcher = KeyMatcher(target_key)

    def background_transcribe(pcm_bytes):
        if not pcm_bytes:
            set_title("claude")
            return
        dur = len(pcm_bytes) / (16000 * 2)
        if dur < 0.4:
            set_title("claude")
            return
        try:
            import numpy as np
            samples = np.frombuffer(pcm_bytes, dtype=np.int16)
            if len(samples) > 0:
                rms = np.sqrt(np.mean(samples.astype(np.float32) ** 2))
                if rms < 100:
                    set_title("claude")
                    return
        except Exception:
            pass

        fd_wav, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd_wav)
        try:
            with wave.open(wav_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(pcm_bytes)

            text = None
            try:
                text = worker_manager.transcribe(wav_path)
            except Exception as e:
                log_error(f"Worker transcription failed, falling back to one-shot: {e}")
                # Last resort fallback to one-shot subprocess
                try:
                    python_exec = "/home/ayush/vscoder/Documents/vscode/transcriber/.venv/bin/python"
                    worker_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transcribe_worker.py")
                    res = subprocess.run([python_exec, worker_script, wav_path], capture_output=True, text=True, timeout=60)
                    if res.returncode == 0:
                        text = res.stdout.strip()
                    else:
                        log_error(f"One-shot fallback failed: {res.stderr}")
                except Exception as ex:
                    log_error(f"One-shot fallback exception: {ex}")

            if text:
                text_clean, send_enter, keep = parse_voice_commands(text)
                if keep and text_clean:
                    payload = b'\x1b[200~' + text_clean.encode('utf-8') + b'\x1b[201~'
                    try:
                        os.write(pty_fd, payload)
                        if send_enter:
                            os.write(pty_fd, b'\r')
                    except Exception as e:
                        log_error(f"Failed to write to pty: {e}")
        except Exception as e:
            log_error(f"Transcription error: {e}")
        finally:
            if os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass
            set_title("claude")

    try:
        while True:
            if recording and (time.time() - recorder.start_time >= max_secs):
                pcm_bytes = recorder.stop()
                recording = False
                ring_bell()
                set_title("[...] transcribing")
                threading.Thread(target=background_transcribe, args=(pcm_bytes,), daemon=True).start()

            rlist, _, _ = select.select([sys.stdin.fileno(), pty_fd], [], [], 0.05)
            
            if sys.stdin.fileno() in rlist:
                try:
                    chunk = os.read(sys.stdin.fileno(), 1024)
                except OSError:
                    break
                if not chunk:
                    break

                i = 0
                while i < len(chunk):
                    byte = chunk[i]
                    i += 1
                    
                    matched, rem = matcher.feed(byte)
                    if rem:
                        os.write(pty_fd, rem)
                    if matched:
                        if not recording:
                            ring_bell()
                            try:
                                recorder.start()
                                recording = True
                                set_title("[REC] claude")
                            except Exception as e:
                                log_error(f"Recording error: {e}")
                                set_title("parecord error")
                                print(f"\r\n[claude-voice error: {e}]\r\n", file=sys.stderr)
                        else:
                            recording = False
                            ring_bell()
                            set_title("[...] transcribing")
                            pcm_bytes = recorder.stop()
                            threading.Thread(target=background_transcribe, args=(pcm_bytes,), daemon=True).start()

                if matcher.buf:
                    more, _, _ = select.select([sys.stdin.fileno()], [], [], 0.05)
                    if not more:
                        os.write(pty_fd, bytes(matcher.buf))
                        matcher.buf.clear()

            if pty_fd in rlist:
                try:
                    data = os.read(pty_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                sys.stdout.flush()

            wpid, status = os.waitpid(pid, os.WNOHANG)
            if wpid == pid:
                if os.WIFEXITED(status):
                    sys.exit(os.WEXITSTATUS(status))
                elif os.WIFSIGNALED(status):
                    sys.exit(128 + os.WTERMSIG(status))

        try:
            _, status = os.waitpid(pid, 0)
            exit_code = os.waitstatus_to_exitcode(status)
        except ChildProcessError:
            exit_code = 0
        sys.exit(exit_code if exit_code >= 0 else 128 - exit_code)
    finally:
        if worker_manager:
            worker_manager.shutdown()
        if has_raw and old_term is not None:
            try:
                termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_term)
            except Exception:
                pass
        if recording:
            try:
                recorder.stop()
            except Exception:
                pass
        set_title("claude")

if __name__ == "__main__":
    main()
