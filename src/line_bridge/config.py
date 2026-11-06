from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import json
import os
import sys
from pathlib import Path
import uuid

from .errors import BridgeError
from .security import atomic_write, check_local_path


@dataclass
class MonitorPolicy:
    enabled: bool = False
    scope: str = "selected"
    targets: list[str] = field(default_factory=list)
    include_future_chats: bool = False
    interval_seconds: int = 120


@dataclass
class NotifyPolicy:
    enabled: bool = False
    direct_instant: bool = True
    instant_chats: list[str] = field(default_factory=list)
    muted_chats: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    debounce_seconds: int = 120
    digest_minutes: int = 60
    quiet_start: int = 0
    quiet_end: int = 7
    utc_offset_minutes: int = 540
    hourly_cap: int = 6


@dataclass
class Config:
    schema_version: int = 1
    profile_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    personal_mode: str = "disabled"
    openchat_mode: str = "disabled"
    unified_generation: str = ""
    login_device: str = "ANDROIDSECONDARY"
    deno_executable: str = ""
    deno_cache_directory: str = ""
    linejs_directory: str = ""
    sending_enabled: bool = True
    monitor: MonitorPolicy = field(default_factory=MonitorPolicy)
    notify: NotifyPolicy = field(default_factory=NotifyPolicy)

    def validate(self) -> None:
        from .devices import normalize_device
        if normalize_device(self.login_device) != self.login_device:
            raise BridgeError("invalid_login_device")
        if self.schema_version != 1:
            raise BridgeError("unsupported_config_version")
        try:
            if str(uuid.UUID(self.profile_id)) != self.profile_id:
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise BridgeError("invalid_profile_id") from None
        for name in ("personal_mode", "openchat_mode"):
            if getattr(self, name) not in ("disabled", "native"):
                raise BridgeError("invalid_provider_mode")
        if self.unified_generation:
            try:
                if str(uuid.UUID(self.unified_generation)) != self.unified_generation:
                    raise ValueError()
            except (ValueError, TypeError, AttributeError):
                raise BridgeError("invalid_unified_generation") from None
            if self.personal_mode != "native" or self.openchat_mode != "native":
                raise BridgeError("unified_login_requires_both_native_providers")
        elif not isinstance(self.unified_generation, str):
            raise BridgeError("invalid_unified_generation")
        if type(self.sending_enabled) is not bool:
            raise BridgeError("invalid_boolean")
        p = self.monitor
        if type(p.enabled) is not bool or type(p.include_future_chats) is not bool:
            raise BridgeError("invalid_monitor_boolean")
        if p.scope not in ("selected", "all"):
            raise BridgeError("invalid_monitor_policy")
        if not isinstance(p.targets, list) or len(p.targets) > 500 or any(not isinstance(x, str) for x in p.targets):
            raise BridgeError("invalid_monitor_targets")
        if type(p.interval_seconds) is not int or not 1 <= p.interval_seconds <= 86400:
            raise BridgeError("invalid_monitor_limits")
        n = self.notify
        if type(n.enabled) is not bool or type(n.direct_instant) is not bool:
            raise BridgeError("invalid_notify_boolean")
        for values in (n.instant_chats, n.muted_chats, n.keywords):
            if not isinstance(values, list) or len(values) > 500 or any(not isinstance(x, str) or len(x) > 250 for x in values):
                raise BridgeError("invalid_notify_lists")
        limits = ((n.debounce_seconds, 0, 3600), (n.digest_minutes, 1, 1440), (n.quiet_start, 0, 23),
                  (n.quiet_end, 0, 23), (n.utc_offset_minutes, -720, 840), (n.hourly_cap, 1, 60))
        if any(type(v) is not int or not lo <= v <= hi for v, lo, hi in limits):
            raise BridgeError("invalid_notify_limits")

    def save(self, root: Path) -> None:
        self.validate()
        atomic_write(root / "config.json", json.dumps(asdict(self), ensure_ascii=False, indent=2).encode("utf-8"))

    @classmethod
    def load(cls, root: Path) -> Config:
        try:
            raw = json.loads((root / "config.json").read_text(encoding="utf-8"))
            # Sending defaults to enabled; an explicit stored setting is kept.
            raw.setdefault("sending_enabled", True)
            raw.setdefault("login_device", "DESKTOPWIN" if raw.get("unified_generation") else "ANDROIDSECONDARY")
            # Settings of removed features (tunnel, notification export) are ignored.
            known = {f.name for f in fields(MonitorPolicy)}
            raw["monitor"] = MonitorPolicy(**{k: v for k, v in raw.get("monitor", {}).items() if k in known})
            known = {f.name for f in fields(NotifyPolicy)}
            raw["notify"] = NotifyPolicy(**{k: v for k, v in raw.get("notify", {}).items() if k in known})
            known = {f.name for f in fields(cls)}
            result = cls(**{k: v for k, v in raw.items() if k in known})
            result.validate()
            return result
        except BridgeError:
            raise
        except (TypeError, ValueError, KeyError, OSError):
            raise BridgeError("invalid_or_missing_config") from None


def default_root() -> Path:
    if sys.platform.startswith("linux"):
        data = os.environ.get("XDG_DATA_HOME")
        base = Path(data) if data else Path.home() / ".local" / "share"
        if not base.is_absolute():
            raise BridgeError("xdg_data_home_must_be_absolute")
        return check_local_path(base / "linebridge" / "profiles" / "default")
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise BridgeError("localappdata_missing")
    return check_local_path(Path(local) / "LineBridge" / "profiles" / "default")
