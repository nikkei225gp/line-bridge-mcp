"""One authoritative tool catalogue; annotations are hints, not permissions."""
from dataclasses import dataclass

from .errors import BridgeError
from .message import AVAILABILITIES, KINDS, REASONS


def string(maximum=2000):
    return {"type": "string", "maxLength": maximum}


def number(minimum, maximum, default):
    return {"type": "integer", "minimum": minimum, "maximum": maximum, "default": default}


REF = string(250)

# Shared output schema for the normalized message object returned by reads and
# archive searches. Field names mirror line_bridge.bridge.normalize_message.
MESSAGE_FIELDS = {
    "source": {"type": "string", "enum": ["personal", "openchat"]},
    "chat_ref": {"type": "string"},
    "chat_name": {"type": "string"},
    "message_ref": {"type": "string"},
    "sender": {"type": "string"},
    "created_ms": {"type": "integer"},
    "kind": {"type": "string", "enum": list(KINDS)},
    "native_type": {"type": "string"},
    "availability": {"type": "string", "enum": list(AVAILABILITIES)},
    "reason": {"anyOf": [{"type": "string", "enum": list(REASONS)}, {"type": "null"}]},
    "plain_text": {"type": "string"},
    "content": {"type": ["object", "null"]},
    "text_truncated": {"type": "boolean"},
    "from_me": {"type": "boolean"},
    "unsent": {"type": "boolean"},
}

MESSAGE_REQUIRED = list(MESSAGE_FIELDS)
# Set only on OpenChat thread replies: the message_ref the thread hangs off.
MESSAGE_FIELDS["thread_root_ref"] = {"type": "string"}
# Set only on OpenChat messages that have a thread; read its replies with openchat_thread_messages.
MESSAGE_FIELDS["thread"] = {"type": "object", "properties": {"reply_count": {"type": "integer"}, "last_reply_ms": {"type": "integer"}}}


def messages_output(chat_ref=False):
    properties = {"messages": {"type": "array", "items": {"type": "object", "properties": MESSAGE_FIELDS, "required": MESSAGE_REQUIRED}}}
    required = ["messages"]
    if chat_ref:
        properties["chat_ref"] = {"type": "string"}
        required.append("chat_ref")
    return {"type": "object", "properties": properties, "required": required}


def thread_output():
    result = messages_output(chat_ref=True)
    result["properties"].update({"thread_root_ref": {"type": "string"}, "thread_exists": {"type": "boolean"}})
    return result


def members_output():
    member = {"type": "object", "properties": {"name": {"type": "string"}, "is_me": {"type": "boolean"},
                                              "is_creator": {"type": "boolean"}, "role": {"type": "string", "enum": ["admin", "co_admin", "member"]}},
              "required": ["name", "is_me"]}
    return {"type": "object", "properties": {"chat_ref": {"type": "string"}, "chat_name": {"type": "string"},
                                              "source": {"type": "string", "enum": ["personal", "openchat"]},
                                              "members": {"type": "array", "items": member},
                                              "total": {"type": ["integer", "null"]}, "truncated": {"type": "boolean"}},
            "required": ["chat_ref", "members", "truncated"]}


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    required: tuple = ()
    source: str = ""
    writable: bool = False
    output_schema: dict = None

    def schema(self):
        result = {"name": self.name, "description": self.description,
                  "inputSchema": {"type": "object", "properties": self.properties,
                                  "required": list(self.required), "additionalProperties": False},
                  "annotations": {"readOnlyHint": not self.writable, "destructiveHint": self.writable,
                                  "idempotentHint": not self.writable, "openWorldHint": bool(self.source)}}
        if self.output_schema is not None:
            result["outputSchema"] = self.output_schema
        return result

    def validate(self, arguments):
        if not isinstance(arguments, dict) or set(arguments) - set(self.properties) or set(self.required) - set(arguments):
            raise BridgeError("invalid_tool_arguments")
        result = dict(arguments)
        for key, spec in self.properties.items():
            if key not in result:
                if "default" in spec:
                    result[key] = spec["default"]
                continue
            value = result[key]
            if spec["type"] == "integer":
                if type(value) is not int or not spec["minimum"] <= value <= spec["maximum"]:
                    raise BridgeError("invalid_tool_arguments")
            elif spec["type"] == "boolean":
                if type(value) is not bool:
                    raise BridgeError("invalid_tool_arguments")
            elif not isinstance(value, str) or len(value) > spec["maxLength"] or (key in self.required and not value):
                raise BridgeError("invalid_tool_arguments")
            if "enum" in spec and value not in spec["enum"]:
                raise BridgeError("invalid_tool_arguments")
        return result


TOOLS = [
    Tool("bridge_status", "State counts only. No names, message text, paths, QR or secrets.", {}),
    Tool("login_start", "Start an explicit QR login. Returns a PNG Base64 image and QR URL; show them to the account owner. Poll login_status for a PIN or completion. Android is default. Never returns saved tokens or private keys.",
         {"device": {**string(32), "enum": ["ANDROIDSECONDARY", "IOSIPAD", "DESKTOPWIN", "DESKTOPMAC"], "default": "ANDROIDSECONDARY"},
          "replace": {"type": "boolean", "default": False}}, writable=True),
    Tool("login_status", "Get the temporary QR PNG Base64, QR URL or PIN for this login. Show PIN as text; the human scans/confirms on their main LINE phone. No automatic re-login.", {"login_id": string(36)}, ("login_id",)),
    Tool("login_cancel", "Cancel this pending QR login without logging out an existing saved session.", {"login_id": string(36)}, ("login_id",), writable=True),
    Tool("personal_chats", "Bounded recent normal LINE chats. Use returned chat_ref, never raw LINE IDs.", {"limit": number(1, 100, 30)}, source="personal"),
    Tool("personal_chat", "Information for an exact normal LINE chat reference.", {"chat_ref": REF}, ("chat_ref",), "personal"),
    Tool("personal_messages", "Read normal LINE without marking read. Each message is classified by kind and availability; flex alt text, sticker text, file names and other allow-listed fields are surfaced. Photos are fetched separately with message_image.", {"chat_ref": REF, "limit": number(1, 200, 20), "before_message_ref": REF}, ("chat_ref",), "personal", output_schema=messages_output(chat_ref=True)),
    Tool("personal_find_contact", "Find a contact by name. Returns opaque contact_ref.", {"name": string(200)}, ("name",), "personal"),
    Tool("personal_contact_chats", "Find the direct chat for a returned contact_ref.", {"contact_ref": REF}, ("contact_ref",), "personal"),
    Tool("personal_groups", "Bounded joined normal LINE groups, without joining or accepting invitations.", {}, source="personal"),
    Tool("openchat_chats", "Bounded confirmed joined OpenChat rooms. Use opaque chat_ref.", {}, source="openchat"),
    Tool("openchat_messages", "Bounded OpenChat event history. Latest N messages are NOT guaranteed. No read receipts. Each message is classified by kind and availability. A message with a thread has thread.reply_count; read the replies with openchat_thread_messages.", {"chat_ref": REF, "limit": number(1, 50, 10)}, ("chat_ref",), "openchat", output_schema=messages_output(chat_ref=True)),
    Tool("openchat_thread_messages", "Bounded replies in the thread under one OpenChat message, by a message_ref from openchat_messages or archive_messages. thread_exists is false when nobody has replied in a thread. Latest N replies are NOT guaranteed. No read receipts and no thread joining.",
         {"message_ref": REF, "limit": number(1, 50, 20)}, ("message_ref",), "openchat", output_schema=thread_output()),
    Tool("archive_messages", "Query permanently saved personal/group/OpenChat messages without LINE access. Optional text substring, chat, timestamps, exact message_ref and pagination. Coverage is only retrieved messages; use next_before_message_ref to continue.", {"chat_ref": REF, "message_ref": REF, "query": string(500), "limit": number(1, 100, 50), "before_message_ref": REF, "after_ms": number(0, 9007199254740991, 0), "before_ms": number(0, 9007199254740991, 9007199254740991)}, output_schema=messages_output()),
    Tool("message_image", "Download one photo posted in a normal LINE or OpenChat chat, by a message_ref of kind image from a read or archive_messages. Videos, audio and files are refused. preview=true returns LINE's smaller thumbnail. Returned as image content; never saved.",
         {"message_ref": REF, "preview": {"type": "boolean", "default": False}}, ("message_ref",), "personal"),
    Tool("chat_members", "Display names of the people in one chat: a normal LINE group or room, a direct chat (you and the contact), or a joined OpenChat room, by chat_ref. Names only, no IDs. Normal LINE marks you and the group creator; OpenChat gives each member's role (admin, co_admin, member). Bounded: total says how many exist, truncated says the list was cut.",
         {"chat_ref": REF, "limit": number(1, 200, 100)}, ("chat_ref",), "personal", output_schema=members_output()),
    Tool("unsend", "Take back (unsend) one of our own messages in normal LINE or OpenChat, thread replies included, by a message_ref from a read, send or archive_messages. LINE only allows this for about 24 hours after sending. No per-message approval. Returns unsent or not_unsent with a reason.",
         {"message_ref": REF}, ("message_ref",), writable=True),
    Tool("openchat_delete_message", "Delete anyone's OpenChat message as a co-admin or admin, by message_ref. LINE refuses it for ordinary members. Returns deleted or not_deleted with a reason.",
         {"message_ref": REF}, ("message_ref",), writable=True),
    Tool("send", "Send exact personal/group/OpenChat text once. No per-message approval. Never retry an unknown outcome with a new UUID. For an OpenChat thread reply, also pass thread_message_ref: the message the thread hangs off, from the same chat_ref; a thread is started if it has none. To quote-reply to a message of the same chat instead, pass reply_to_message_ref (a message_ref from a read, send or archive_messages); the result says whether LINE kept the quote (reply_confirmed). A quote reply and a thread reply cannot be combined.", {"chat_ref": REF, "text": string(5000), "request_id": string(36), "thread_message_ref": REF, "reply_to_message_ref": REF}, ("chat_ref", "text", "request_id"), writable=True),
]


WRITES = ("send", "unsend", "openchat_delete_message")


def available(config, role):
    if role != "local":
        raise BridgeError("invalid_role")
    return {t.name: t for t in TOOLS if t.name not in WRITES or config.sending_enabled}
