"""Protocol identities supplied by the already pinned LINEJS dependency."""
DEVICES = {
    "ANDROIDSECONDARY": "26.6.2",
    "IOSIPAD": "26.7.2",
    "DESKTOPWIN": "26.5.0.3975",
    "DESKTOPMAC": "26.2.0",
}
ALIASES = {"android": "ANDROIDSECONDARY", "ipad": "IOSIPAD", "windows": "DESKTOPWIN", "mac": "DESKTOPMAC"}


def normalize_device(value):
    from .errors import BridgeError
    result = ALIASES.get(value, value)
    if result not in DEVICES:
        raise BridgeError("unsupported_login_device")
    return result
