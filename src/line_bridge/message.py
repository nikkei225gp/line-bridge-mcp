"""Message content model: kind, native type, availability and safe extraction.

The JS adapters surface a native LINE content type plus the message's content
metadata. This module maps the native type to a coarse kind, and extracts only a
bounded, allow-listed subset of that metadata as structured content. Raw
upstream metadata never crosses into the result. Media bytes are not part of a
message; only a posted photo can be fetched on request (Bridge.message_image).
"""
from __future__ import annotations

import json

# LINE Talk/Square ContentType enum: numeric value -> canonical name.
_NUMERIC = {
    0: "NONE", 1: "IMAGE", 2: "VIDEO", 3: "AUDIO", 4: "HTML", 5: "PDF",
    6: "CALL", 7: "STICKER", 8: "PRESENCE", 9: "GIFT", 10: "GROUPBOARD",
    11: "APPLINK", 12: "LINK", 13: "CONTACT", 14: "FILE", 15: "LOCATION",
    16: "POSTNOTIFICATION", 17: "RICH", 18: "CHATEVENT", 19: "MUSIC",
    20: "PAYMENT", 21: "EXTIMAGE", 22: "FLEX",
}

# Canonical native type name -> coarse kind. Anything unmapped becomes "unknown".
KIND_BY_TYPE = {
    "NONE": "text",
    "TEXT": "text",
    "HTML": "text",
    "IMAGE": "image",
    "EXTIMAGE": "image",
    "VIDEO": "video",
    "AUDIO": "audio",
    "MUSIC": "audio",
    "PDF": "file",
    "FILE": "file",
    "STICKER": "sticker",
    "CONTACT": "contact",
    "LOCATION": "location",
    "FLEX": "flex",
    "RICH": "rich",
    "APPLINK": "link",
    "LINK": "link",
}

KINDS = ("text", "image", "video", "audio", "file", "sticker",
         "contact", "location", "flex", "rich", "link", "unknown")

AVAILABILITIES = ("complete", "partial", "unavailable")

REASONS = ("payload_not_exposed", "media_not_retrieved",
           "decrypt_failed", "unsupported_content")

MAX_TEXT = 20000
MAX_METADATA = 2000
MAX_FLEX = 20000

_DECRYPT_FAILED = "[encrypted message"


def normalize_native_type(value):
    """Return a canonical native type name for whatever the adapter produced.

    The thrift codec returns enum names ("FLEX", "IMAGE", ...), while some
    paths and fixtures pass numeric values (0, 1, 22, ...). Both are folded to
    the canonical uppercase name, and an empty or missing type becomes "".
    """
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return _NUMERIC.get(value, str(value))
    s = str(value).strip()
    if not s:
        return ""
    upper = s.upper()
    if upper == "TEXT":
        upper = "NONE"
    if upper in KIND_BY_TYPE:
        return upper
    if s.isdigit():
        return _NUMERIC.get(int(s), s)
    if s.startswith("-") and s[1:].isdigit():
        return _NUMERIC.get(int(s), s)
    return upper


def kind_of(native_type):
    return KIND_BY_TYPE.get(native_type, "unknown")


def bounded_text(value):
    """Truncate a readable string to MAX_TEXT and report whether it was cut."""
    text = "" if value is None else str(value)
    return text[:MAX_TEXT], len(text) > MAX_TEXT


def _metadata_str(metadata, key):
    if not isinstance(metadata, dict):
        return ""
    value = metadata.get(key)
    if value is None:
        return ""
    return str(value)[:MAX_METADATA]


def _bounded_flex(value):
    """Return a JSON-serializable flex structure or None when unusable/large."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
    else:
        return None
    try:
        encoded = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return parsed if len(encoded) <= MAX_FLEX else None


def classify(raw_text, native_type, metadata):
    """Return (kind, plain_text, content, availability, reason).

    plain_text is the best-effort readable text: the body for text messages,
    the alt text for flex, and so on. content is a bounded, allow-listed
    structured object or None. reason explains an unavailable payload without
    exposing upstream internals.
    """
    kind = kind_of(native_type)
    metadata = metadata if isinstance(metadata, dict) else {}
    text = "" if raw_text is None else str(raw_text)

    if text.startswith(_DECRYPT_FAILED):
        return kind, "", None, "unavailable", "decrypt_failed"

    if kind == "text":
        return kind, text, None, "complete", None

    if kind == "flex":
        alt = _metadata_str(metadata, "ALT_TEXT")
        flex_json = _bounded_flex(metadata.get("FLEX_JSON"))
        ver = _metadata_str(metadata, "FLEX_VER")
        tag = _metadata_str(metadata, "EFFECT_TAG")
        plain = alt or text
        content = {}
        if alt:
            content["alt_text"] = alt
        if flex_json is not None:
            content["flex_json"] = flex_json
        if ver:
            content["ver"] = ver
        if tag:
            content["effect_tag"] = tag
        if not content:
            content = None
        if flex_json is not None:
            availability = "complete"
        elif plain:
            availability = "partial"
        else:
            availability = "unavailable"
        reason = None if (flex_json is not None or plain) else "payload_not_exposed"
        return kind, plain, content, availability, reason

    if kind == "rich":
        alt = _metadata_str(metadata, "ALT_TEXT")
        preview = _metadata_str(metadata, "PREVIEW_URL")
        download = _metadata_str(metadata, "DOWNLOAD_URL")
        plain = alt or text
        content = {}
        if alt:
            content["alt_text"] = alt
        if preview:
            content["preview_url"] = preview
        if download:
            content["download_url"] = download
        if not content:
            content = None
        availability = "complete" if plain else "unavailable"
        reason = None if plain else ("media_not_retrieved" if download else "payload_not_exposed")
        return kind, plain, content, availability, reason

    if kind == "sticker":
        plain = _metadata_str(metadata, "STKTXT")
        package_id = _metadata_str(metadata, "STKPKGID")
        sticker_id = _metadata_str(metadata, "STKID")
        ver = _metadata_str(metadata, "STKVER")
        content = {}
        if package_id:
            content["package_id"] = package_id
        if sticker_id:
            content["sticker_id"] = sticker_id
        if ver:
            content["version"] = ver
        if plain:
            content["text"] = plain
        if not content:
            content = None
        availability = "complete" if content else "unavailable"
        reason = None if content else "payload_not_exposed"
        return kind, plain, content, availability, reason

    if kind == "file":
        plain = _metadata_str(metadata, "FILE_NAME")
        size = _metadata_str(metadata, "FILE_SIZE")
        content = {}
        if plain:
            content["name"] = plain
        if size:
            content["size"] = size
        if not content:
            content = None
        availability = "complete" if plain else "unavailable"
        reason = None if plain else ("media_not_retrieved" if _metadata_str(metadata, "DOWNLOAD_URL") else "payload_not_exposed")
        return kind, plain, content, availability, reason

    if kind == "contact":
        plain = _metadata_str(metadata, "displayName")
        mid = _metadata_str(metadata, "mid")
        content = {}
        if mid:
            content["mid"] = mid
        if plain:
            content["display_name"] = plain
        if not content:
            content = None
        availability = "complete" if content else "unavailable"
        reason = None if content else "payload_not_exposed"
        return kind, plain, content, availability, reason

    if kind == "location":
        title = _metadata_str(metadata, "TITLE")
        address = _metadata_str(metadata, "ADDRESS") or _metadata_str(metadata, "SUB_TEXT")
        latitude = _metadata_str(metadata, "LATITUDE")
        longitude = _metadata_str(metadata, "LONGITUDE")
        plain = title or text
        content = {}
        if title:
            content["title"] = title
        if address:
            content["address"] = address
        if latitude:
            content["latitude"] = latitude
        if longitude:
            content["longitude"] = longitude
        if not content:
            content = None
        availability = "complete" if content else "unavailable"
        reason = None if content else "payload_not_exposed"
        return kind, plain, content, availability, reason

    if kind == "link":
        url = (_metadata_str(metadata, "LINK") or _metadata_str(metadata, "URL")
               or _metadata_str(metadata, "link"))
        plain = text
        content = {"url": url} if url else None
        availability = "complete" if (plain or url) else "unavailable"
        reason = None if (plain or url) else "payload_not_exposed"
        return kind, plain, content, availability, reason

    if kind in ("image", "video", "audio"):
        return kind, "", None, "unavailable", "media_not_retrieved"

    # Unknown native type: surface whatever readable text is present, honestly
    # labelled unknown, without guessing the kind.
    if text:
        return kind, text, None, "complete", None
    return kind, "", None, "unavailable", "unsupported_content"


def image_mime(data):
    """The MIME type of a photo by its magic bytes, or None for anything else."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None
