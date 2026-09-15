"""Canonical project identity, atomic state and process-safe transactions."""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import uuid


class StackError(Exception):
    pass


def canonical(path: Path) -> Path:
    return Path(os.path.normcase(str(path.expanduser().resolve())))


def project_root(path: Path) -> Path:
    path = canonical(path)
    if not path.is_dir():
        raise StackError(f"Project directory does not exist: {path}")
    # The folder opened by this Codex session is authoritative, including a
    # subfolder inside a larger Git checkout. Never silently widen indexing.
    return path


def read_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def safe_path(path: Path):
    # Refuse symlink/junction redirection, including Windows directory junctions.
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise StackError(f"Refusing redirected managed path: {part}")


def atomic_write(path: Path, data: bytes | None):
    safe_path(path)
    if read_bytes(path) == data:
        return
    if data is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    # tempfile.mkstemp can retry PermissionError TMP_MAX times on Windows when
    # os.access incorrectly reports a sandbox-denied directory as writable.
    # A unique exclusive create fails promptly and preserves fail-open hooks.
    name = path.parent / (".efficiency-" + uuid.uuid4().hex)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path, default=None):
    if not path.exists():
        return {} if default is None else default
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise ValueError("expected object")
        return result
    except (ValueError, OSError) as exc:
        raise StackError(f"Cannot read state {path}: {exc}") from exc


def write_json(path: Path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def default_home():
    if os.environ.get("CODEX_SAVER_HOME"):
        return Path(os.environ["CODEX_SAVER_HOME"])
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home()/"AppData/Local"))/"CodexTokenSaver"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home()/".local/share"))/"codex-token-saver"


class Store:
    def __init__(self, root=None, project=None, codex_home=None):
        self.root = canonical(Path(root or default_home()))
        self.project = project_root(Path(project or Path.cwd()))
        configured = read_json(self.root/"settings.json").get("codex_home") if (self.root/"settings.json").is_file() else None
        self.codex_home = canonical(Path(codex_home or os.environ.get("CODEX_HOME") or configured or Path.home()/".codex"))
        self.directory = self.root/"projects"/hashlib.sha256(str(self.project).encode()).hexdigest()[:24]
        self.state_path = self.root/"settings.json"
        self.session_id = None
    def read(self):
        return {"schema": 2, **read_json(self.state_path)}
    def enabled(self):
        try:
            return self.read().get("enabled") is True
        except StackError:
            return False
    def active(self, component):
        return component in ("rtk", "cce") and self.enabled()
    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root/"control.lock").open("a+b") as f:
            f.seek(0)
            if os.name == "nt":
                import msvcrt
                if not f.read(1):
                    f.write(b"0"); f.flush()
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                f.seek(0)
                if os.name == "nt":
                    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
