from pathlib import Path
import os

from .errors import BridgeError


class ProcessLock:
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        if os.name == "nt":
            self.stream = self.path.open("a+b")
        else:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            self.stream = os.fdopen(fd, "a+b")
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            self.stream = None
            raise BridgeError("profile_already_running") from None
        return self

    def __exit__(self, *args):
        if self.stream:
            self.stream.close()
            self.stream = None
