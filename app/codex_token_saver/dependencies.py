"""Pinned optional engines; configuration is private, never cce init."""
import hashlib
import io
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from .state import StackError, atomic_write, read_json, write_json

RTK_VERSION = "0.48.0"
CCE_VERSION = "0.4.26"
ASSETS = Path(__file__).parent/"assets"
ARCHIVES = {
    "win32": ("rtk-x86_64-pc-windows-msvc.zip", "8c9ae56bacde865112777a9fe9791b449186d8b2a081c32c0772ef773f284f93"),
    "linux": ("rtk-x86_64-unknown-linux-musl.tar.gz", "e4e650fa1677c0de2f6839a6040d7b17f312d32f163c402b75af70e9e5af1a91")}


def manifest(store):
    return read_json(store.root/"dependencies.json")


def detect_codex():
    found = shutil.which("codex")
    if found:
        return found
    # VS Code's normal extension install is also a supported local CLI source.
    pattern = "openai.chatgpt-*/bin/windows-x86_64/codex.exe" if os.name == "nt" else "openai.chatgpt-*/bin/linux-x86_64/codex"
    candidates = sorted((Path.home()/".vscode/extensions").glob(pattern), reverse=True)
    return str(candidates[0]) if candidates else None


def original_rtk(directory):
    if platform.machine().lower() not in ("amd64", "x86_64") or sys.platform not in ARCHIVES:
        raise StackError("Beta supports Windows/Linux x86_64")
    name, expected = ARCHIVES[sys.platform]
    data = urllib.request.urlopen(f"https://github.com/rtk-ai/rtk/releases/download/v{RTK_VERSION}/{name}", timeout=120).read()
    if hashlib.sha256(data).hexdigest() != expected:
        raise StackError("RTK download SHA-256 mismatch")
    filename = "rtk.exe" if os.name == "nt" else "rtk"
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            candidates = [p for p in archive.namelist() if Path(p).name == filename]
            if len(candidates) != 1:
                raise StackError("Unexpected RTK archive")
            payload = archive.read(candidates[0])
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            candidates = [p for p in archive.getmembers() if p.isfile() and Path(p.name).name == filename]
            if len(candidates) != 1:
                raise StackError("Unexpected RTK archive")
            payload = archive.extractfile(candidates[0]).read()
    target = Path(directory)/filename
    atomic_write(target, payload)
    target.chmod(0o755)
    return str(target)


def setup(store):
    store.root.mkdir(parents=True, exist_ok=True)
    binary = Path(sys.executable).with_name("cce.exe" if os.name == "nt" else "cce")
    if not binary.exists():
        raise StackError("CCE missing: installer must install the pinned engine extra")
    deps = {"codex": detect_codex(), "cce": str(binary), "rtk": original_rtk(store.root/"engines")}
    write_json(store.root/"dependencies.json", deps)
    if not deps["codex"]:
        raise StackError("Codex CLI not found. Install Codex, then rerun the installer.")
    return deps


def private_env(store):
    env = os.environ.copy()
    private = store.root/"engine-home"
    private.mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(private), USERPROFILE=str(private), XDG_CONFIG_HOME=str(private/"config"),
        XDG_DATA_HOME=str(private/"data"), XDG_CACHE_HOME=str(private/"cache"), APPDATA=str(private/"appdata"),
        LOCALAPPDATA=str(private/"localappdata"), CCE_EMBED_BACKEND="fastembed", CCE_OLLAMA_URL="http://127.0.0.1:11434",
        CCE_FASTEMBED_CACHE_PATH=str(private/"models"), HF_HUB_DISABLE_TELEMETRY="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("CCE_REMOTE_URL", None)
    config = private/".cce/config.yaml"
    policy = b'compression:\n  output: "off"\nmemory:\n  redact_pii: true\naudit:\n  enabled: false\n'
    if config.exists() and config.read_bytes() != policy:
        raise StackError("Private CCE configuration changed; preserving it")
    atomic_write(config, policy)
    return env
