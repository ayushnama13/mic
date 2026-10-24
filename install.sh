#!/usr/bin/env bash
set -e

BIN_DIR="$HOME/bin"
mkdir -p "$BIN_DIR"

TARGET="$BIN_DIR/claude-voice"
PYTHON="/home/ayush/vscoder/Documents/vscode/transcriber/.venv/bin/python"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="$PROJECT_DIR/claude_voice.py"

cat << EOF > "$TARGET"
#!/usr/bin/env bash
exec "$PYTHON" "$SCRIPT_PATH" "\$@"
EOF

chmod +x "$TARGET"
echo "Installed claude-voice to $TARGET"
