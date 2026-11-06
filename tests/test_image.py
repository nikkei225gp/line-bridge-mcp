import json
import unittest

from line_bridge.errors import BridgeError
from line_bridge.rpc import Rpc
from support import environment


class ImageTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def read(self, source, rows):
        if source == "personal":
            ref = self.bridge.personal_chats()["chats"][0]["chat_ref"]
        else:
            ref = self.bridge.openchat_listing()["chats"][0]["chat_ref"]
        self.providers[source].rows = rows
        return ref, {m["plain_text"] or m["kind"]: m["message_ref"] for m in self.bridge.messages(source, ref, 10)["messages"]}

    def test_personal_photo_is_returned_once_as_image_content(self):
        ref, messages = self.read("personal", [{"id": "101", "type": 1, "text": "", "created_ms": 10}])
        response = Rpc(self.bridge).dispatch("local", "tools/call", {"name": "message_image",
                                                                      "arguments": {"message_ref": messages["image"]}})
        self.assertFalse(response["isError"])
        text, image = response["content"]
        self.assertEqual((image["type"], image["mimeType"]), ("image", "image/png"))
        result = json.loads(text["text"])
        self.assertNotIn("image_base64", result)
        self.assertNotIn("image_base64", response["structuredContent"])
        self.assertEqual((result["chat_ref"], result["bytes"], result["preview"], result["saved_locally"]), (ref, 15, False, False))
        self.assertEqual(self.providers["personal"].calls[-1], ("image", {
            "source": "personal", "chat_id": "normal-room", "message_id": "101", "created_ms": 10, "preview": False}))

    def test_openchat_photo_and_preview(self):
        _, messages = self.read("openchat", [{"id": "202", "content_type": "IMAGE", "text": None, "created_ms": 20}])
        self.providers["openchat"].image_bytes = b"\xff\xd8\xff\xe0fixture"
        result = self.bridge.call("local", "message_image", {"message_ref": messages["image"], "preview": True})
        self.assertEqual((result["source"], result["mime_type"]), ("openchat", "image/jpeg"))
        self.assertEqual(self.providers["openchat"].calls[-1][1]["preview"], True)

    def test_other_kinds_and_unsaved_messages_are_refused_before_download(self):
        _, messages = self.read("personal", [{"id": "1", "type": 2, "text": "", "created_ms": 1},
                                             {"id": "2", "type": 14, "text": "", "created_ms": 2,
                                              "content_metadata": {"FILE_NAME": "fixture.pdf"}},
                                             {"id": "3", "type": 0, "text": "hello", "created_ms": 3}])
        for ref in messages.values():
            with self.assertRaisesRegex(BridgeError, "not_an_image"):
                self.bridge.call("local", "message_image", {"message_ref": ref})
        with self.assertRaisesRegex(BridgeError, "message_not_retrieved"):
            self.bridge.call("local", "message_image", {"message_ref": "message:v1:unknown"})
        self.assertNotIn("image", [c[0] for c in self.providers["personal"].calls])

    def test_non_image_bytes_are_not_returned(self):
        _, messages = self.read("personal", [{"id": "101", "type": 1, "text": "", "created_ms": 10}])
        self.providers["personal"].image_bytes = b"\x00\x00\x00\x18ftypmp42"
        with self.assertRaisesRegex(BridgeError, "not_an_image"):
            self.bridge.call("local", "message_image", {"message_ref": messages["image"]})


if __name__ == "__main__":
    unittest.main()
