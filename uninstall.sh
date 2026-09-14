#!/bin/sh
set -eu
CTS_HOME=${CODEX_SAVER_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/codex-token-saver}
exec "$CTS_HOME/venv/bin/python" -m codex_token_saver --home "$CTS_HOME" uninstall
