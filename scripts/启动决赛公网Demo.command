#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
export PATH="/opt/homebrew/bin:$HOME/.npm-global/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
cd "$PROJECT_DIR"
exec /opt/homebrew/bin/python3 "$SCRIPT_DIR/start_competition_gateway.py"
