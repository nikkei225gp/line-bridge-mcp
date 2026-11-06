import unittest

from line_bridge.message import classify, kind_of, normalize_native_type
from support import environment


class MessageModelTests(unittest.TestCase):
    def test_native_type_folds_enum_names_and_numerics(self):
        self.assertEqual(normalize_native_type("FLEX"), "FLEX")
        self.assertEqual(normalize_native_type(22), "FLEX")
        self.assertEqual(normalize_native_type("22"), "FLEX")
        self.assertEqual(normalize_native_type("TEXT"), "NONE")
        self.assertEqual(normalize_native_type("image"), "IMAGE")
        self.assertEqual(normalize_native_type(None), "")
        self.assertEqual(normalize_native_type(True), "")

    def test_kinds_are_coarse(self):
        self.assertEqual(kind_of("NONE"), "text")
        self.assertEqual(kind_of("IMAGE"), "image")
        self.assertEqual(kind_of("FLEX"), "flex")
        self.assertEqual(kind_of("RICH"), "rich")
        self.assertEqual(kind_of("STICKER"), "sticker")
        self.assertEqual(kind_of("FILE"), "file")
        self.assertEqual(kind_of("SOMETHING_NEW"), "unknown")

    def test_flex_surfaces_alt_text_and_structured_json(self):
        kind, plain, content, availability, reason = classify(
            None, "FLEX", {"ALT_TEXT": "代替テキスト", "FLEX_JSON": '{"type":"bubble"}', "FLEX_VER": "1"})
        self.assertEqual((kind, plain, availability, reason), ("flex", "代替テキスト", "complete", None))
        self.assertEqual(content["alt_text"], "代替テキスト")
        self.assertEqual(content["flex_json"], {"type": "bubble"})

    def test_flex_without_json_is_partial(self):
        kind, plain, content, availability, reason = classify(
            None, "FLEX", {"ALT_TEXT": "代替のみ"})
        self.assertEqual((kind, plain, availability, reason), ("flex", "代替のみ", "partial", None))
        self.assertNotIn("flex_json", content)

    def test_flex_without_anything_is_unavailable(self):
        kind, plain, content, availability, reason = classify(None, "FLEX", {})
        self.assertEqual((kind, plain, availability, reason), ("flex", "", "unavailable", "payload_not_exposed"))
        self.assertIsNone(content)

    def test_rich_and_sticker_and_file_extraction(self):
        self.assertEqual(classify(None, "RICH", {"ALT_TEXT": "p", "DOWNLOAD_URL": "u"})[2],
                         {"alt_text": "p", "download_url": "u"})
        self.assertEqual(classify(None, "STICKER", {"STKTXT": "絵文字", "STKPKGID": "1", "STKID": "2"})[1], "絵文字")
        self.assertEqual(classify(None, "FILE", {"FILE_NAME": "report.pdf"})[1], "report.pdf")

    def test_media_and_decrypt_are_labelled(self):
        self.assertEqual(classify("", "IMAGE", {})[3:], ("unavailable", "media_not_retrieved"))
        self.assertEqual(classify("[encrypted message unavailable]", "NONE", {})[3:], ("unavailable", "decrypt_failed"))

    def test_unknown_type_keeps_text_but_stays_unknown(self):
        kind, plain, content, availability, reason = classify("some body", "FUTURE_TYPE", {})
        self.assertEqual((kind, plain, availability, reason), ("unknown", "some body", "complete", None))

    def test_normalize_message_produces_classified_shape(self):
        temp, root, config, protector, store, providers, bridge = environment()
        try:
            ref = bridge.personal_chats()["chats"][0]["chat_ref"]
            message = bridge.normalize_message("personal", ref, "Fixture room", {
                "id": "flex-1", "created_ms": 5, "content_type": "FLEX", "text": None,
                "content_metadata": {"ALT_TEXT": "カード", "FLEX_JSON": '{"type":"bubble"}'},
            }, providers["personal"].generation)
            self.assertEqual(message["kind"], "flex")
            self.assertEqual(message["native_type"], "FLEX")
            self.assertEqual(message["availability"], "complete")
            self.assertEqual(message["plain_text"], "カード")
            self.assertEqual(message["content"]["flex_json"], {"type": "bubble"})
        finally:
            bridge.close()
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
