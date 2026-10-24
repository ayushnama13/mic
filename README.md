# claude-voice

Voice-dictation PTY wrapper for the Claude Code CLI.

## Requirements
- Python 3.10+
- faster-whisper (uses transcriber venv interpreter)
- `parecord` (PulseAudio / PipeWire)

## Installation
```bash
./install.sh
```

## Usage
Run `claude-voice` in your terminal (passes arguments through to `claude`).
- The Whisper model is preloaded on GPU for the lifetime of the wrapper (VRAM held ~1.5 GB while open) and freed on exit.
- Press **F9** (default) to start voice recording.
- Press **F9** again to stop recording and insert the transcribed text into Claude Code using bracketed paste.
- Voice commands:
  - `"send it"` at the end: sends Enter after inserting text.
  - `"scratch that"` at the end: discards text.
  - `"new line"` anywhere: inserts a newline.
