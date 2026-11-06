import json
import unittest

from line_bridge.rpc import Rpc
from support import environment


class MembersTests(unittest.TestCase):
    def setUp(self):
        self.temp, self.root, self.config, self.protector, self.store, self.providers, self.bridge = environment()

    def tearDown(self):
        self.bridge.close()
        self.temp.cleanup()

    def call(self, arguments):
        return Rpc(self.bridge).dispatch("local", "tools/call", {"name": "chat_members", "arguments": arguments})

    def test_personal_members_are_names_without_ids(self):
        ref = self.bridge.personal_chats()["chats"][0]["chat_ref"]
        response = self.call({"chat_ref": ref})
        self.assertFalse(response["isError"])
        result = response["structuredContent"]
        self.assertEqual(result["members"], [{"name": "Fixture user", "is_me": True, "is_creator": False},
                                             {"name": "Fixture friend", "is_me": False, "is_creator": True}])
        self.assertEqual((result["source"], result["chat_name"], result["total"], result["truncated"]), ("personal", "Fixture normal room", 2, False))
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(self.providers["personal"].calls[-1], ("members", {"chat_id": "normal-room", "limit": 100}))

    def test_openchat_members_have_roles(self):
        ref = self.bridge.openchat_listing()["chats"][0]["chat_ref"]
        self.providers["openchat"].members_result = {"members": [{"name": "A", "role": "co_admin", "is_me": False},
                                                                 {"name": "B", "role": "unexpected", "is_me": True}], "truncated": True}
        result = self.call({"chat_ref": ref, "limit": 2})["structuredContent"]
        self.assertEqual(result["members"], [{"name": "A", "is_me": False, "role": "co_admin"}, {"name": "B", "is_me": True, "role": "member"}])
        self.assertEqual((result["source"], result["total"], result["truncated"]), ("openchat", None, True))
        self.assertEqual(self.providers["openchat"].calls[-1], ("members", {"chat_id": "open-room", "limit": 2}))

    def test_extra_rows_are_cut_and_bad_input_is_refused(self):
        ref = self.bridge.personal_chats()["chats"][0]["chat_ref"]
        self.providers["personal"].members_result = {"members": [{"name": str(i), "is_me": False} for i in range(5)], "total": 5, "truncated": True}
        self.assertEqual(len(self.call({"chat_ref": ref, "limit": 3})["structuredContent"]["members"]), 3)
        self.providers["personal"].members_result = {"members": "not a list"}
        self.assertTrue(self.call({"chat_ref": ref})["isError"])
        for arguments in ({"chat_ref": ref, "limit": 0}, {"chat_ref": ref, "limit": 201}, {"chat_ref": ref, "chat": "x"}, {}):
            self.assertTrue(self.call(arguments)["isError"])
        self.assertTrue(self.call({"chat_ref": "chat:v1:unknown"})["isError"])


if __name__ == "__main__":
    unittest.main()
