from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spark_web.storage import Store
import spark_web.server as server


class TalkLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(path=Path(self.tmp.name) / "test.db")
        self._patchers = [patch.object(server, "STORE", self.store)]
        for p in self._patchers:
            p.start()
        server._LOGIN_ATTEMPTS.clear()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.tmp.cleanup()

    def _add_user(self, username: str, password: str, role: str = "viewer",
                  forum_username: str | None = None, enabled: int = 1):
        with self.store.transaction() as conn:
            conn.execute("INSERT INTO users VALUES(?,?,?,?,?,?)",
                         (username, self.store.hash_password(password), role, forum_username, enabled,
                          "2026-11-18T00:00:00"))

    def test_exact_committee_accounts_are_admitted_after_forum_verification(self):
        names = ("xingtianchunyan", "zz_tovarishch", "Hanssen", "yixiu.ckbfans.bit")
        self.assertEqual(server.legacy.COMMITTEE_MEMBERS, {name.casefold() for name in names})
        with patch.object(server, "verify_talk_credentials", return_value=True) as verify:
            for name in names:
                with self.subTest(name=name):
                    user = server._resolve_login_user(" @" + name + " ", "forum-fixture")
                    self.assertEqual(user["role"], "viewer")
                    self.assertEqual(user["forum_username"], name.casefold())
                    verify.assert_called_with(name.casefold(), "forum-fixture")
            server._resolve_login_user("HANSSEN", "forum-fixture")
        self.assertEqual(len(self.store.query("SELECT * FROM users")), 4)

    def test_old_invite_list_and_preexisting_outsider_account_grant_no_access(self):
        self._add_user("outsider", "local-pass", role="administrator", forum_username="outsider")
        with patch.dict("os.environ", {"SPARK_TEAM_ALLOWED_TALK_USERS": "outsider"}), \
             patch.object(server, "verify_talk_credentials", return_value=True) as verify:
            self.assertIsNone(server._resolve_login_user("@outsider", "local-pass"))
            verify.assert_not_called()

    def test_local_password_does_not_bypass_talk_verification(self):
        self._add_user("xingtianchunyan", "local-pass", role="administrator", forum_username="xingtianchunyan")
        with patch.object(server, "verify_talk_credentials", return_value=False) as verify:
            self.assertIsNone(server._resolve_login_user("xingtianchunyan", "local-pass"))
            verify.assert_called_once_with("xingtianchunyan", "local-pass")

    def test_only_explicitly_bound_account_keeps_privileged_role(self):
        self._add_user("local-admin", "local-pass", role="administrator", forum_username="Xingtianchunyan")
        self._add_user("Hanssen", "local-pass", role="approver")
        with patch.object(server, "verify_talk_credentials", return_value=True):
            user = server._resolve_login_user("xingtianchunyan", "forum-fixture")
            self.assertEqual((user["username"], user["role"]), ("local-admin", "administrator"))
            self.assertIsNone(server._resolve_login_user("Hanssen", "forum-fixture"))

    def test_disabled_account_and_empty_password_do_not_contact_talk(self):
        self._add_user("reviewer", "local-pass", forum_username="Hanssen", enabled=0)
        with patch.object(server, "verify_talk_credentials", return_value=True) as verify:
            self.assertIsNone(server._resolve_login_user("@hanssen", "forum-fixture"))
            self.assertIsNone(server._resolve_login_user("xingtianchunyan", ""))
            verify.assert_not_called()

    def test_login_throttle_blocks_after_eight_attempts(self):
        for _ in range(8):
            self.assertFalse(server._login_throttled("10.0.0.1"))
        self.assertTrue(server._login_throttled("10.0.0.1"))
        self.assertFalse(server._login_throttled("10.0.0.2"))


class VerifyTalkCredentialsTests(unittest.TestCase):
    def test_empty_input_rejected_without_network(self):
        from spark_web import forum
        self.assertFalse(forum.verify_talk_credentials("", "x"))
        self.assertFalse(forum.verify_talk_credentials("alice", ""))

    def test_wrong_password_returns_false(self):
        from spark_web import forum
        with patch.object(forum.TalkSession, "login", side_effect=PermissionError):
            self.assertFalse(forum.verify_talk_credentials("member", "wrong-test-password"))


if __name__ == "__main__":
    unittest.main()
