"""Windows DPAPI or Linux AES-GCM with an owner-only profile key. No plaintext fallback."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
from typing import Protocol

from .errors import BridgeError


class Protector(Protocol):
    def seal(self, value: bytes) -> bytes: ...
    def open(self, value: bytes) -> bytes: ...


class DPAPI:
    def __init__(self, profile_id: str):
        if os.name != "nt":
            raise BridgeError("windows_required")
        self.entropy = ("line-bridge:v1:" + profile_id).encode("ascii")

    def seal(self, value: bytes) -> bytes:
        import win32crypt
        return win32crypt.CryptProtectData(value, "LineBridge", self.entropy, None, None, 1)

    def open(self, value: bytes) -> bytes:
        import win32crypt
        try:
            return win32crypt.CryptUnprotectData(value, self.entropy, None, None, 1)[1]
        except Exception:
            raise BridgeError("dpapi_unavailable_for_this_user_or_machine") from None


def verify_private_file(path: Path):
    value = path.lstat()
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid()
            or stat.S_IMODE(value.st_mode) != 0o600 or value.st_nlink != 1):
        raise BridgeError("unsafe_private_file")


def read_private_file(path: Path) -> bytes:
    verify_private_file(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        value = os.fstat(stream.fileno())
        if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid()
                or stat.S_IMODE(value.st_mode) != 0o600 or value.st_nlink != 1):
            raise BridgeError("unsafe_private_file")
        return stream.read()


class LinuxProtector:
    prefix = b"LBAES1\0"

    def __init__(self, root: Path, profile_id: str, create=False):
        if not sys.platform.startswith("linux"):
            raise BridgeError("linux_required")
        verify_acl(root)
        path = root / "profile-key.bin"
        if create:
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(secrets.token_bytes(32))
                    stream.flush()
                    os.fsync(stream.fileno())
            except FileExistsError:
                raise BridgeError("profile_key_already_exists") from None
        try:
            key = read_private_file(path)
        except FileNotFoundError:
            raise BridgeError("profile_key_missing_no_automatic_replacement") from None
        if len(key) != 32:
            raise BridgeError("invalid_profile_key")
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        self.cipher = AESGCM(key)
        self.entropy = ("line-bridge:linux:v1:" + profile_id).encode("ascii")

    def seal(self, value: bytes) -> bytes:
        nonce = secrets.token_bytes(12)
        return self.prefix + nonce + self.cipher.encrypt(nonce, value, self.entropy)

    def open(self, value: bytes) -> bytes:
        size = len(self.prefix)
        if not value.startswith(self.prefix) or len(value) < size + 28:
            raise BridgeError("unsupported_secret_format")
        try:
            return self.cipher.decrypt(value[size:size + 12], value[size + 12:], self.entropy)
        except Exception:
            raise BridgeError("secret_decryption_failed") from None


def profile_protector(root: Path, profile_id: str, create=False) -> Protector:
    if os.name == "nt":
        return DPAPI(profile_id)
    if sys.platform.startswith("linux"):
        return LinuxProtector(root, profile_id, create=create)
    raise BridgeError("unsupported_operating_system")


def check_local_path(path: Path) -> Path:
    path = path.absolute()
    if str(path).startswith(("\\\\", "//")):
        raise BridgeError("network_data_directory_refused")
    for parent in (path, *path.parents):
        if parent.exists() and (parent.is_symlink() or (
            os.name == "nt" and ctypes.windll.kernel32.GetFileAttributesW(str(parent)) & 0x400
        )):
            raise BridgeError("reparse_data_directory_refused")
    return path


def current_sid() -> str:
    import win32api
    import win32con
    import win32security
    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token, win32security.TokenUser)[0])
    finally:
        token.Close()


def verify_acl(path: Path) -> None:
    if sys.platform.startswith("linux"):
        path = check_local_path(path)
        value = path.lstat()
        if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o700:
            raise BridgeError("unsafe_profile_permissions")
        for child in path.iterdir():
            if child.is_dir() and not child.is_symlink():
                verify_acl(child)
            else:
                verify_private_file(child)
        return
    if os.name != "nt":
        raise BridgeError("windows_required")
    import win32security
    descriptor = win32security.GetNamedSecurityInfo(str(path), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION)
    dacl = descriptor.GetSecurityDescriptorDacl()
    if dacl is None:
        raise BridgeError("unsafe_acl")
    allowed = {current_sid(), "S-1-5-18"}
    seen = set()
    for i in range(dacl.GetAceCount()):
        ace = dacl.GetAce(i)
        if ace[0][0] != win32security.ACCESS_ALLOWED_ACE_TYPE:
            raise BridgeError("unsupported_acl_entry")
        sid = win32security.ConvertSidToStringSid(ace[2])
        if sid not in allowed:
            raise BridgeError("unexpected_acl_principal")
        seen.add(sid)
    if seen != allowed:
        raise BridgeError("incomplete_acl")


def create_private_directory(path: Path) -> Path:
    path = check_local_path(path)
    if sys.platform.startswith("linux"):
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                path.mkdir(mode=0o700)
            except FileExistsError:
                pass
        verify_acl(path)
        return path
    if os.name != "nt":
        raise BridgeError("windows_required")
    if path.exists():
        verify_acl(path)
        return path
    import pywintypes
    import win32file
    import win32security
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(
        "D:P(A;OICI;FA;;;" + current_sid() + ")(A;OICI;FA;;;SY)",
        win32security.SDDL_REVISION_1)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = descriptor
    try:
        # The protected DACL exists at creation; no inherited-ACL window for secrets.
        win32file.CreateDirectory(str(path), attributes)
    except Exception:
        raise BridgeError("acl_setup_failed") from None
    verify_acl(path)
    return path


def atomic_write(path: Path, body: bytes) -> None:
    temp = path.with_name(path.name + "." + secrets.token_hex(8) + ".tmp")
    try:
        if sys.platform.startswith("linux"):
            check_local_path(path)
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            stream = os.fdopen(fd, "wb")
        else:
            stream = temp.open("xb")
        with stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_secret(path: Path, value, protector: Protector) -> None:
    atomic_write(path, protector.seal(json.dumps(value, ensure_ascii=False).encode("utf-8")))


def load_secret(path: Path, protector: Protector):
    body = read_private_file(path) if sys.platform.startswith("linux") else path.read_bytes()
    return json.loads(protector.open(body))
