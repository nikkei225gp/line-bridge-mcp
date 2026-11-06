"""OS-specific process flags without changing LINE's saved protocol identity."""
import os
import subprocess


def process_options(detached=False):
    if os.name == 'nt':
        flags = subprocess.CREATE_NO_WINDOW
        if detached:
            flags |= subprocess.DETACHED_PROCESS
        return {'creationflags': flags}
    return {'start_new_session': True} if detached else {}


def worker_environment():
    allowed = ('SYSTEMROOT', 'WINDIR', 'LOCALAPPDATA', 'TEMP', 'TMP', 'DENO_DIR',
               'HOME', 'PATH', 'TMPDIR', 'XDG_CACHE_HOME')
    return {key: os.environ[key] for key in allowed if key in os.environ}
