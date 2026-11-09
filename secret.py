#!/usr/bin/env python3
# Copyright (c) 2026 Peter Kokosharov
# SPDX-License-Identifier: MIT
# Author: Peter Kokosharov <peter.kokosharov@gmail.com>
"""Portable secret tool: one interface over native credential stores.

The tool stores secrets under stable key paths: ``<name>/<name>/kind`` (e.g. ``nvidia/personal/api_key`` or ``personal/my-project/github_token``).
The native store of the operating system is primary.  A plain ``KEY_PATH | value``
export file is supported, but it is a temporary transfer/backup file, not a second
source of truth.

The native backends come from the vendored ``lib/kikolib`` copy:

* ``kikolib.keystore`` — tested Windows Credential Manager ctypes layer, plus the
  Keychain, ``secret-tool``, KWallet and TDE DCOP calls;
* ``kikolib.factory`` — registration, probes and backend selection.

kikolib namespaces native entries under ``KIKO`` and upper-cases the name; this tool
normalises the names back to lower-case key paths when it lists them.  kikolib has no
list operation of its own, so the list commands stay in this file.

Design rules
------------
* standard library only;
* ``auto`` never falls back to a plaintext file -- use ``--backend file`` explicitly;
* ``list``, ``probe`` and ``setup`` never print secret values;
* values are never logged or included in exception messages;
* ``set`` reads the value from stdin or a prompt, never from argv;
* export files are written atomically with mode ``600``;
* ``run`` injects a value into a child process environment without printing it.

Exit codes: 0 ok, 1 not found, 2 backend error, 3 usage error.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_BACKEND = 2
EXIT_USAGE = 3

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

try:
    from kikolib import factory as kfactory
    from kikolib import keystore as kkeystore

    KIKOLIB_AVAILABLE = True
    KIKOLIB_ERROR = ""
except ImportError as exc:  # pragma: no cover - only on a broken checkout
    kfactory = None
    kkeystore = None
    KIKOLIB_AVAILABLE = False
    KIKOLIB_ERROR = str(exc)

DEFAULT_EXPORT = Path.home() / "SECRETS"
SERVICE = "portable-workspace"
KWALLET_WALLET = os.environ.get("KWALLET_WALLET", "kdewallet")
KWALLET_FOLDER = os.environ.get("KWALLET_FOLDER", "Passwords")

MAX_PATH = 255
_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# --------------------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------------------

class SecretError(Exception):
    """Base error with a process exit code."""

    exit_code = EXIT_BACKEND

    def __init__(self, message: str, exit_code: int | None = None):
        super().__init__(message)
        if exit_code is not None:
            self.exit_code = exit_code


class UsageError(SecretError):
    exit_code = EXIT_USAGE


class NotFoundError(SecretError):
    exit_code = EXIT_NOT_FOUND


class BackendError(SecretError):
    exit_code = EXIT_BACKEND


class ExportFormatError(BackendError):
    """Malformed export/import file."""


# --------------------------------------------------------------------------------------
# key paths and values
# --------------------------------------------------------------------------------------

def validate_path(path: str) -> str:
    """Validate and return a key path (two or more parts, e.g. ``nvidia/personal/api_key``)."""
    if not isinstance(path, str) or not path:
        raise UsageError("key path is empty")
    if len(path) > MAX_PATH:
        raise UsageError(f"key path is longer than {MAX_PATH} characters")
    if path.startswith("/") or path.endswith("/"):
        raise UsageError(f"invalid key path {path!r}: leading or trailing '/'")
    if "\\" in path or any(ch.isspace() for ch in path):
        raise UsageError(f"invalid key path {path!r}: whitespace and backslashes are not allowed")
    parts = path.split("/")
    if len(parts) < 2:
        raise UsageError(f"invalid key path {path!r}: at least two segments are required")
    for part in parts:
        if part == "..":
            raise UsageError(f"invalid key path {path!r}: '..' is not allowed")
        if not _SEGMENT_RE.match(part):
            raise UsageError(
                f"invalid key path {path!r}: segment {part!r} must match "
                "[a-z0-9][a-z0-9_-]*"
            )
    return path


def validate_value(value: str) -> str:
    """Values are single-line because the export format is line based."""
    if not isinstance(value, str) or value == "":
        raise UsageError("secret value is empty")
    if "\0" in value or "\n" in value or "\r" in value:
        raise UsageError("secret value must be a single line without NUL characters")
    return value


def env_name_for(path: str) -> str:
    """Derive a safe environment variable name from a key path."""
    name = re.sub(r"[^A-Za-z0-9]+", "_", path).upper().strip("_")
    if not name or name[0].isdigit():
        name = "SECRET_" + name
    return name


# --------------------------------------------------------------------------------------
# export file format: KEY_PATH | value
# --------------------------------------------------------------------------------------

def parse_export(text: str, source: str = "<input>") -> dict[str, str]:
    """Parse ``KEY_PATH | value`` lines.  Split on the first ``|`` only."""
    entries: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" not in raw:
            raise ExportFormatError(f"{source}:{number}: missing '|' separator")
        key_part, value_part = raw.split("|", 1)
        key = key_part.strip()
        value = value_part.strip()
        try:
            validate_path(key)
            validate_value(value)
        except UsageError as exc:
            raise ExportFormatError(f"{source}:{number}: {exc}") from None
        if key in entries:
            raise ExportFormatError(f"{source}:{number}: duplicate key path {key!r}")
        entries[key] = value
    return entries


def format_export(entries: dict[str, str]) -> str:
    lines = ["# KEY_PATH | value"]
    for key in sorted(entries):
        lines.append(f"{key} | {entries[key]}")
    return "\n".join(lines) + "\n"


def atomic_write_text(path: Path, text: str, mode: int = 0o600) -> None:
    """Write ``text`` atomically; ``path`` ends up with ``mode`` on POSIX."""
    path = Path(path).expanduser()
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(parent))
    tmp = Path(tmp_name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        if hasattr(os, "chmod"):
            try:
                os.chmod(path, mode)
            except OSError:
                pass
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------------------
# small helpers over kikolib.keystore
# --------------------------------------------------------------------------------------

def _combined(result) -> str:
    return (result.stdout or "") + (result.stderr or "")


def _detail(result) -> str:
    if result is None:
        return "command unavailable"
    lines = (result.stderr or "").strip().splitlines()
    if lines:
        return lines[-1][:200]
    data = (result.stdout or "").strip().splitlines()
    if data:
        return data[-1][:200]
    return f"exit code {result.returncode}"


def _find_dcop() -> str | None:
    found = shutil.which("dcop")
    if found:
        return found
    for candidate in (
        "/opt/trinity/bin/dcop",
        "/opt/kde3/bin/dcop",
        "/usr/lib/trinity/dcop",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _parse_dcop_list(text: str) -> list[str]:
    text = (text or "").strip()
    if not text or text == "-1":
        return []
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = re.findall(r'"([^"]*)"', text) or text.split()
    return [token for token in tokens if token]


# --------------------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------------------

class Backend:
    name = "base"
    supports_delete = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, path: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def set(self, path: str, value: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def delete(self, path: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def names(self) -> list[str]:  # pragma: no cover - interface
        raise NotImplementedError


class MemoryBackend(Backend):
    """In-memory backend, useful for tests and dry runs."""

    name = "memory"

    def __init__(self, initial: dict[str, str] | None = None):
        self._data = dict(initial or {})

    def get(self, path: str) -> str:
        if path not in self._data:
            raise NotFoundError(f"no such key: {path}")
        return self._data[path]

    def set(self, path: str, value: str) -> None:
        self._data[path] = value

    def delete(self, path: str) -> None:
        if path not in self._data:
            raise NotFoundError(f"no such key: {path}")
        del self._data[path]

    def names(self) -> list[str]:
        return sorted(self._data)


class FileBackend(Backend):
    """Temporary ``KEY_PATH | value`` export file."""

    name = "file"

    def __init__(self, path: Path | str = DEFAULT_EXPORT):
        self.path = Path(path).expanduser()

    def _load(self) -> dict[str, str]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise BackendError(f"cannot read {self.path}: {exc.strerror}") from exc
        return parse_export(text, str(self.path))

    def _save(self, entries: dict[str, str]) -> None:
        atomic_write_text(self.path, format_export(entries))

    def get(self, path: str) -> str:
        entries = self._load()
        if path not in entries:
            raise NotFoundError(f"no such key: {path}")
        return entries[path]

    def set(self, path: str, value: str) -> None:
        entries = self._load()
        entries[path] = value
        self._save(entries)

    def delete(self, path: str) -> None:
        entries = self._load()
        if path not in entries:
            raise NotFoundError(f"no such key: {path}")
        del entries[path]
        self._save(entries)

    def names(self) -> list[str]:
        return sorted(self._load())


class WcmBackend(Backend):
    """Windows Credential Manager through the tested kikolib ctypes layer."""

    name = "wcm"

    @staticmethod
    def probe() -> tuple[bool, str]:
        if not KIKOLIB_AVAILABLE:
            return False, f"kikolib missing: {KIKOLIB_ERROR}"
        return kkeystore._probe_wcm()

    @classmethod
    def usable(cls) -> bool:
        return cls.probe()[0]

    @staticmethod
    def _store():
        return kkeystore.WcmStore()

    def get(self, path: str) -> str:
        value = self._store().get(path)
        if value is None:
            raise NotFoundError(f"no such key: {path}")
        return value

    def set(self, path: str, value: str) -> None:
        if not self._store().set(path, value):
            raise BackendError(f"CredWrite failed for {path}")

    def delete(self, path: str) -> None:
        if not self._store().delete(path):
            raise NotFoundError(f"no such key: {path}")

    def names(self) -> list[str]:
        if sys.platform != "win32":
            raise BackendError("Windows Credential Manager is only available on Windows")
        import ctypes
        from ctypes import wintypes

        cred_t = kkeystore._wcm_struct()
        prefix = kkeystore.WcmStore._target("")
        adv = ctypes.WinDLL("advapi32", use_last_error=True)
        count = wintypes.DWORD()
        ptr = ctypes.POINTER(ctypes.POINTER(cred_t))()
        if not adv.CredEnumerateW("*", 0, ctypes.byref(count), ctypes.byref(ptr)):
            return []
        try:
            names = []
            for index in range(count.value):
                target = ptr[index].contents.TargetName
                if not target or not target.startswith(prefix):
                    continue
                try:
                    names.append(validate_path(target[len(prefix) :].lower()))
                except UsageError:
                    continue
            return sorted(set(names))
        finally:
            adv.CredFree(ptr)


class KeychainBackend(Backend):
    """macOS Keychain through kikolib's ``security`` wrapper."""

    name = "keychain"

    @staticmethod
    def probe() -> tuple[bool, str]:
        if not KIKOLIB_AVAILABLE:
            return False, f"kikolib missing: {KIKOLIB_ERROR}"
        return kkeystore._probe_keychain()

    @classmethod
    def usable(cls) -> bool:
        return cls.probe()[0]

    def get(self, path: str) -> str:
        value = kkeystore.KeychainStore().get(path)
        if value is None:
            raise NotFoundError(f"no such key: {path}")
        return value

    def set(self, path: str, value: str) -> None:
        if not kkeystore.KeychainStore().set(path, value):
            raise BackendError(f"security add-generic-password failed for {path}")

    def delete(self, path: str) -> None:
        if not kkeystore.KeychainStore().delete(path):
            raise NotFoundError(f"no such key: {path}")

    def names(self) -> list[str]:
        result = kkeystore._run(["security", "dump-keychain"])
        if result is None or result.returncode != 0:
            raise BackendError(f"security dump-keychain failed: {_detail(result)}")
        marker = '"svce"<blob>="KIKO"'
        found = set()
        for block in re.split(r"\n(?=keychain: )", result.stdout or ""):
            if marker not in block:
                continue
            match = re.search(r'"acct"<blob>="([^"]+)"', block)
            if not match:
                continue
            try:
                found.add(validate_path(match.group(1).lower()))
            except UsageError:
                continue
        return sorted(found)


class SecretToolBackend(Backend):
    """GNOME Keyring through kikolib's ``secret-tool`` wrapper.

    The value travels on stdin.  An extra ``service`` attribute is added so that
    entries can be listed without knowing every key path in advance.
    """

    name = "secret-tool"

    @staticmethod
    def probe() -> tuple[bool, str]:
        if not KIKOLIB_AVAILABLE:
            return False, f"kikolib missing: {KIKOLIB_ERROR}"
        return kkeystore._probe_secret_tool()

    @classmethod
    def usable(cls) -> bool:
        return cls.probe()[0]

    def get(self, path: str) -> str:
        value = kkeystore.SecretToolStore().get(path)
        if value is None:
            raise NotFoundError(f"no such key: {path}")
        return value

    def set(self, path: str, value: str) -> None:
        result = kkeystore._run(
            [
                "secret-tool",
                "store",
                "--label",
                f"{SERVICE} {path}",
                "kiko-identity",
                path.upper(),
                "service",
                SERVICE,
            ],
            stdin=value,
        )
        if not kkeystore._ok(result):
            raise BackendError(f"secret-tool store failed: {_detail(result)}")

    def delete(self, path: str) -> None:
        if not kkeystore.SecretToolStore().delete(path):
            raise NotFoundError(f"no such key: {path}")

    def names(self) -> list[str]:
        result = kkeystore._run(["secret-tool", "search", "--all", "service", SERVICE])
        if result is None:
            raise BackendError("secret-tool is not available")
        if result.returncode == 1:
            return []
        if result.returncode != 0:
            raise BackendError(f"secret-tool search failed: {_detail(result)}")
        found = set()
        # secret-tool prints "attribute.kiko-identity = X" (older builds: no prefix).
        for line in (result.stdout or "").splitlines():
            key, sep, rest = line.partition(" = ")
            if not sep or key.strip().removeprefix("attribute.") != "kiko-identity":
                continue
            candidate = rest.strip().lower()
            try:
                found.add(validate_path(candidate))
            except UsageError:
                continue
        return sorted(found)


class KWalletBackend(Backend):
    """KWallet through kikolib's ``kwallet-query`` wrapper.

    ``kwallet-query`` cannot delete entries; deletion is reported as unsupported.
    """

    name = "kwallet"
    supports_delete = False

    @staticmethod
    def probe() -> tuple[bool, str]:
        if not KIKOLIB_AVAILABLE:
            return False, f"kikolib missing: {KIKOLIB_ERROR}"
        ok, reason = kkeystore._probe_kwallet()
        if not ok:
            return ok, reason
        result = kkeystore._run(["kwallet-query", "-l", KWALLET_WALLET])
        if (
            result is not None
            and result.returncode == 0
            and "not found" not in _combined(result).lower()
        ):
            return True, f"KWallet {KWALLET_WALLET}"
        return False, f"wallet {KWALLET_WALLET} not found or locked"

    @classmethod
    def usable(cls) -> bool:
        return cls.probe()[0]

    def get(self, path: str) -> str:
        value = kkeystore.KWalletStore().get(path)
        if value is None:
            raise NotFoundError(f"no such key: {path}")
        return value

    def set(self, path: str, value: str) -> None:
        if not kkeystore.KWalletStore().set(path, value):
            raise BackendError(f"kwallet-query write failed for {path}")

    def delete(self, path: str) -> None:
        raise BackendError(
            "kwallet-query cannot delete entries; use the wallet manager "
            "or the dcop backend"
        )

    def names(self) -> list[str]:
        result = kkeystore._run(
            ["kwallet-query", "-l", "-f", KWALLET_FOLDER, KWALLET_WALLET]
        )
        if result is None or result.returncode != 0:
            raise BackendError(f"kwallet-query list failed: {_detail(result)}")
        found = set()
        for line in (result.stdout or "").splitlines():
            try:
                found.add(validate_path(line.strip().lower()))
            except UsageError:
                continue
        return sorted(found)


_DcopBase = kkeystore.DcopStore if KIKOLIB_AVAILABLE else Backend


class DcopBackend(_DcopBase, Backend):
    """TDE wallet through kikolib's DCOP call loop.

    kikolib's ``DcopStore`` opens ``kdewallet``, uses the ``Passwords`` folder and
    closes the wallet after each call.  The key path is passed unchanged (kikolib's
    own store would upper-case it).  The daemon is reached through ``dcop``; on a
    TDE system the binary may live in ``/opt/trinity/bin`` and not be on ``PATH``.
    """

    name = "dcop"
    _TARGETS = (
        ["kded", "tdewalletd"],
        ["kded", "kwalletd"],
        ["tdewalletd"],
        ["kwalletd"],
    )

    def __init__(self):
        if not KIKOLIB_AVAILABLE:
            raise BackendError(f"kikolib missing: {KIKOLIB_ERROR}")
        dcop = _find_dcop()
        if dcop is None:
            raise BackendError("dcop not found")
        bin_dir = str(Path(dcop).parent)
        path = os.environ.get("PATH", "")
        if bin_dir not in path.split(os.pathsep):
            os.environ["PATH"] = bin_dir + os.pathsep + path if path else bin_dir

    @classmethod
    def probe(cls) -> tuple[bool, str]:
        if not KIKOLIB_AVAILABLE:
            return False, f"kikolib missing: {KIKOLIB_ERROR}"
        if sys.platform in ("win32", "darwin"):
            return False, "not Linux"
        dcop = _find_dcop()
        if dcop is None:
            return False, "dcop not found"
        for target in cls._TARGETS:
            result = kkeystore._run([dcop, *target])
            if (
                result is not None
                and result.returncode == 0
                and "not accessible" not in _combined(result).lower()
            ):
                return True, f"TDE wallet via dcop {' '.join(target)}"
        return False, "TDE wallet DCOP object is not running"

    @classmethod
    def usable(cls) -> bool:
        return cls.probe()[0]

    def get(self, path: str) -> str:
        value = self._call("readPassword", path)
        if not value or value == "-1":
            raise NotFoundError(f"no such key: {path}")
        return value

    def set(self, path: str, value: str) -> None:
        if self._call("writePassword", path, value) != "0":
            raise BackendError(f"dcop writePassword failed for {path}")

    def delete(self, path: str) -> None:
        if self._call("removeEntry", path) != "0":
            raise NotFoundError(f"no such key: {path}")

    def names(self) -> list[str]:
        found = set()
        for candidate in _parse_dcop_list(self._call("entryList") or ""):
            try:
                found.add(validate_path(candidate))
            except UsageError:
                continue
        return sorted(found)


# --------------------------------------------------------------------------------------
# backend selection through kikolib.factory
# --------------------------------------------------------------------------------------

BACKENDS = {
    "file": FileBackend,
    "wcm": WcmBackend,
    "keychain": KeychainBackend,
    "secret-tool": SecretToolBackend,
    "kwallet": KWalletBackend,
    "dcop": DcopBackend,
}


def _register_backends() -> None:
    if not KIKOLIB_AVAILABLE:
        return
    for name, backend_class, priority in (
        ("wcm", WcmBackend, 10),
        ("keychain", KeychainBackend, 10),
        ("secret-tool", SecretToolBackend, 20),
        ("kwallet", KWalletBackend, 30),
        ("dcop", DcopBackend, 40),
    ):
        kfactory.register(
            "secret-store",
            name,
            probe=backend_class.probe,
            load=backend_class,
            priority=priority,
        )


_register_backends()


def make_backend(name: str, file_path: Path | str = DEFAULT_EXPORT) -> Backend:
    if name == "file":
        return FileBackend(file_path)
    if not KIKOLIB_AVAILABLE:
        raise BackendError(f"kikolib is not available in {LIB}: {KIKOLIB_ERROR}")
    try:
        if name == "auto":
            return kfactory.choose("secret-store")
        return kfactory.choose("secret-store", override=name)
    except (LookupError, RuntimeError) as exc:
        if name == "auto":
            raise BackendError(
                "no usable native credential store; use --backend file explicitly "
                "for a temporary export file"
            ) from None
        raise BackendError(str(exc)) from None


# --------------------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------------------

def _read_value(args) -> str:
    if args.stdin or not sys.stdin.isatty():
        value = sys.stdin.read()
    else:
        value = getpass.getpass(f"Value for {args.path}: ")
        again = getpass.getpass("Repeat: ")
        if value != again:
            raise UsageError("values do not match")
    value = value.rstrip("\r\n")
    return validate_value(value)


def cmd_get(args, backend: Backend) -> int:
    path = validate_path(args.path)
    value = backend.get(path)
    sys.stdout.write(value)
    if not value.endswith("\n"):
        sys.stdout.write("\n")
    return EXIT_OK


def cmd_set(args, backend: Backend) -> int:
    path = validate_path(args.path)
    value = _read_value(args)
    if args.no_clobber:
        try:
            backend.get(path)
        except NotFoundError:
            pass
        else:
            raise SecretError(f"key already exists: {path}", EXIT_NOT_FOUND)
    backend.set(path, value)
    if not getattr(args, "quiet", False):
        print(f"set {path}")
    return EXIT_OK


def cmd_delete(args, backend: Backend) -> int:
    path = validate_path(args.path)
    backend.delete(path)
    if not getattr(args, "quiet", False):
        print(f"deleted {path}")
    return EXIT_OK


def cmd_list(args, backend: Backend) -> int:
    names = backend.names()
    if args.json:
        print(json.dumps(names, ensure_ascii=False, indent=2))
    else:
        for name in names:
            print(name)
    return EXIT_OK


def cmd_export(args, backend: Backend) -> int:
    names = backend.names()
    entries = {name: backend.get(name) for name in names}
    text = format_export(entries)
    if args.out == "-":
        sys.stdout.write(text)
        return EXIT_OK
    path = Path(args.out).expanduser()
    atomic_write_text(path, text)
    if not getattr(args, "quiet", False):
        print(f"exported {len(entries)} key paths to {path}")
    return EXIT_OK


def cmd_import(args, backend: Backend) -> int:
    if args.inp == "-":
        text = sys.stdin.read()
        source = "<stdin>"
        source_path = None
    else:
        source_path = Path(args.inp).expanduser()
        try:
            text = source_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BackendError(f"cannot read {source_path}: {exc.strerror}") from exc
        source = str(source_path)
    entries = parse_export(text, source)
    for path, value in entries.items():
        backend.set(path, value)
    if args.delete and source_path is not None:
        try:
            source_path.unlink()
        except OSError as exc:
            raise BackendError(f"cannot delete {source_path}: {exc.strerror}") from exc
    if not getattr(args, "quiet", False):
        print(f"imported {len(entries)} key paths from {source}")
    return EXIT_OK


def _probe_backend(backend: Backend) -> int:
    if not backend.supports_delete:
        entries = backend.names()
        print(
            f"ok: {backend.name} (read-only probe; {len(entries)} key paths; "
            "delete is not supported)"
        )
        return EXIT_OK
    path = f"probe/{uuid.uuid4().hex[:12]}/value"
    value = "probe-" + uuid.uuid4().hex
    backend.set(path, value)
    try:
        read_back = backend.get(path)
        if read_back != value:
            raise BackendError("probe read-back mismatch")
    finally:
        try:
            backend.delete(path)
        except SecretError:
            pass
    print(f"ok: {backend.name} (write, read and delete)")
    return EXIT_OK


def cmd_probe(args, backend: Backend) -> int:
    if backend.name == "file":
        with tempfile.TemporaryDirectory(prefix="secret-probe-") as tmp:
            return _probe_backend(FileBackend(Path(tmp) / "SECRETS"))
    return _probe_backend(backend)


def cmd_run(args, backend: Backend) -> int:
    path = validate_path(args.path)
    value = backend.get(path)
    env_name = args.env or env_name_for(path)
    if not _ENV_RE.match(env_name):
        raise UsageError(f"invalid environment variable name: {env_name!r}")
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise UsageError(
            "no command given; use: secret.py run <path> -- <command> [args...]"
        )
    env = os.environ.copy()
    env[env_name] = value
    import subprocess

    try:
        completed = subprocess.run(command, env=env)
    except FileNotFoundError:
        raise BackendError(f"command not found: {command[0]}") from None
    except OSError as exc:
        raise BackendError(f"cannot run {command[0]}: {exc.strerror}") from None
    return completed.returncode


def cmd_setup(args, backend: Backend | None = None) -> int:
    print(f"platform: {sys.platform}")
    print(f"export file (temporary): {Path(getattr(args, 'file', DEFAULT_EXPORT)).expanduser()}")
    if not KIKOLIB_AVAILABLE:
        print(f"kikolib: missing ({KIKOLIB_ERROR})")
        return EXIT_BACKEND
    print("native stores (priority order):")
    for entry in kfactory.report():
        if entry["kind"] != "secret-store":
            continue
        for candidate in entry["candidates"]:
            mark = "yes" if candidate["ok"] else "no "
            print(f"  {mark:3} {candidate['name']:12} {candidate['reason']}")
        print(f"auto would use: {entry['chosen'] or 'none'}")
        if not entry["chosen"]:
            print(
                "next: enable a native store, or use --backend file explicitly "
                "for a temporary export file"
            )
    print("probe: python3 tools/secret.py probe")
    return EXIT_OK


# --------------------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------------------

class _Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    common = _Parser(add_help=False)
    common.add_argument(
        "--backend",
        choices=["auto", *BACKENDS],
        default=argparse.SUPPRESS,
        help="credential store (default: auto; auto never uses a plaintext file)",
    )
    common.add_argument(
        "--file",
        default=argparse.SUPPRESS,
        help=f"export file for --backend file (default: {DEFAULT_EXPORT})",
    )
    common.add_argument(
        "--quiet",
        action="store_true",
        default=argparse.SUPPRESS,
        help="suppress status messages",
    )

    parser = _Parser(
        prog="secret.py",
        description="Portable secret tool over native credential stores.",
        parents=[common],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("get", parents=[common], help="print a secret value")
    p.add_argument("path")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("set", parents=[common], help="store a secret value")
    p.add_argument("path")
    p.add_argument("--stdin", action="store_true", help="read the value from stdin")
    p.add_argument(
        "--no-clobber",
        action="store_true",
        help="fail if the key path already exists",
    )
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("del", parents=[common], help="delete a secret")
    p.add_argument("path")
    p.set_defaults(func=cmd_delete)

    p = sub.add_parser("list", parents=[common], help="list key paths (never values)")
    p.add_argument("--json", action="store_true", help="output a JSON array")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("export", parents=[common], help="write a KEY_PATH | value export")
    p.add_argument(
        "--out",
        default="-",
        help="output file, or '-' for stdout (default: -)",
    )
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("import", parents=[common], help="read a KEY_PATH | value export")
    p.add_argument(
        "--in",
        dest="inp",
        default="-",
        help="input file, or '-' for stdin (default: -)",
    )
    p.add_argument(
        "--delete",
        action="store_true",
        help="delete the input file after a successful import",
    )
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("probe", parents=[common], help="test a store without real secrets")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("setup", parents=[common], help="detect the available native store")
    p.set_defaults(func=cmd_setup)

    for name in ("run", "exec"):
        p = sub.add_parser(
            name,
            parents=[common],
            help="run a command with the secret in its environment",
        )
        p.add_argument("path")
        p.add_argument("--env", help="environment variable name (derived from the path)")
        p.add_argument("command", nargs=argparse.REMAINDER)
        p.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        backend_name = getattr(args, "backend", "auto")
        file_path = Path(getattr(args, "file", str(DEFAULT_EXPORT))).expanduser()
        if args.command == "setup":
            return cmd_setup(args)
        backend = make_backend(backend_name, file_path)
        with backend:
            return args.func(args, backend)
    except SecretError as exc:
        print(f"secret.py: {exc}", file=sys.stderr)
        return exc.exit_code
    except KeyboardInterrupt:
        print("secret.py: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
