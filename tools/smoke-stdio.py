"""Synthetic SDK integration: three stdio clients share one local broker. No LINE."""
import asyncio
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from line_bridge.config import Config
from line_bridge.bridge import Bridge
from line_bridge.security import profile_protector, create_private_directory, save_secret
from line_bridge.storage import Store
from line_bridge.transport import serve
from support import FakeProvider


async def client(root):
    env = dict(os.environ)
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        env[key] = str(root.parent)
    server = StdioServerParameters(command=sys.executable, args=["-B", str(ROOT / "bin" / "linebridge.py"),
                                  "--data-dir", str(root), "stdio"], env=env)
    async with stdio_client(server) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {"bridge_status", "personal_chats"} <= names
            result = await session.call_tool("personal_chats", {})
            assert not result.isError
            return result.structuredContent["chats"][0]["chat_ref"]


def main():
    with tempfile.TemporaryDirectory(prefix="linebridge-stdio-") as temp:
        root = create_private_directory(Path(temp) / "profile")
        config = Config(personal_mode="native", openchat_mode="native")
        config.save(root)
        protector = profile_protector(root, config.profile_id, create=True)
        for role in ("local", "admin"):
            save_secret(root / (role + "-key.dpapi"), "fixture-" + role + "-" * 48, protector)
        providers = {s: FakeProvider(s) for s in ("personal", "openchat")}
        bridge = Bridge(config, Store(root, config.profile_id, protector), providers)
        stop, ready = threading.Event(), threading.Event()
        thread = threading.Thread(target=serve, args=(root, protector, bridge, stop, lambda _: ready.set()))
        thread.start()
        try:
            assert ready.wait(10)
            async def check():
                first, second, third = await asyncio.gather(client(root), client(root), client(root))
                assert first == second == third
            asyncio.run(check())
            assert not config.monitor.enabled
            assert providers["openchat"].send_calls == []
            print("SDK stdio smoke: 3 local clients passed; no LINE access")
        finally:
            stop.set()
            thread.join(10)


if __name__ == "__main__":
    main()
