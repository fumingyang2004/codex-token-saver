#!/bin/sh
set -eu
CTS_BUNDLE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
CTS_HOME=${CODEX_SAVER_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/codex-token-saver}
CTS_PYTHON=''
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(not (3,11)<=sys.version_info[:2]<(3,14))' 2>/dev/null; then CTS_PYTHON=$candidate; break; fi
done
if [ -z "$CTS_PYTHON" ]; then
  [ "$(uname -m)" = x86_64 ] || { echo 'Linux x86_64 required'; exit 1; }
  mkdir -p "$CTS_HOME/runtime"
  curl -fLsS https://github.com/astral-sh/uv/releases/download/0.8.22/uv-x86_64-unknown-linux-gnu.tar.gz -o "$CTS_HOME/runtime/uv.tar.gz"
  printf '%s  %s\n' 741ff1f5742c5a4a25d2f829e8395355e43f7a5ae2ebc6368e9ae2df0efb69cf "$CTS_HOME/runtime/uv.tar.gz" | sha256sum -c -
  tar -xzf "$CTS_HOME/runtime/uv.tar.gz" -C "$CTS_HOME/runtime"
  export UV_PYTHON_INSTALL_DIR="$CTS_HOME/runtime/python" UV_CACHE_DIR="$CTS_HOME/runtime/cache"
  "$CTS_HOME/runtime/uv-x86_64-unknown-linux-gnu/uv" python install --no-bin 3.13.7
  CTS_PYTHON=$("$CTS_HOME/runtime/uv-x86_64-unknown-linux-gnu/uv" python find --managed-python 3.13.7)
fi
exec "$CTS_PYTHON" "$CTS_BUNDLE/scripts/bootstrap.py" --home "$CTS_HOME" "$@"
