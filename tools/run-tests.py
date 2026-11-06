"""Offline runner: deny non-loopback networking, block child processes, isolate HOME."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

root = Path(__file__).resolve().parents[1]
if sys.platform.startswith("linux"):
    os.umask(0o077)
sys.path[:0] = [str(root / "src"), str(root / "tests")]
old_cwd = Path.cwd()
connect = socket.socket.connect
getaddrinfo = socket.getaddrinfo


def local_connect(sock, address):
    if isinstance(address, tuple) and address[0] not in ("127.0.0.1", "::1", "localhost"):
        raise RuntimeError("Offline tests: external network disabled")
    return connect(sock, address)


def local_dns(host, *args, **kwargs):
    if host not in (None, "127.0.0.1", "::1", "localhost"):
        raise RuntimeError("Offline tests: external DNS disabled")
    return getaddrinfo(host, *args, **kwargs)


with tempfile.TemporaryDirectory(prefix="linebridge-tests-") as temp:
    try:
        os.chdir(temp)
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
            os.environ[key] = temp
        os.environ["LINE_SESSION_PATH"] = str(Path(temp) / "not-a-session.json")
        os.environ["LINE_MCP_DB"] = str(Path(temp) / "not-a-history.db")
        with patch.object(socket.socket, "connect", local_connect), patch.object(socket, "getaddrinfo", local_dns), \
             patch.object(subprocess, "Popen", side_effect=RuntimeError("Offline tests: subprocess disabled")):
            result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(str(root / "tests")))
    finally:
        os.chdir(old_cwd)
sys.exit(0 if result.wasSuccessful() else 1)
