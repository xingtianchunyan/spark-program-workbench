import unittest
from email.message import Message
from unittest.mock import patch

import spark_web.server as server


def handler_with_headers(**values):
    handler = object.__new__(server.Handler)
    headers = Message()
    for name, value in values.items():
        headers[name.replace("_", "-")] = value
    handler.headers = headers
    return handler


class SecurityTests(unittest.TestCase):
    def test_local_mode_accepts_loopback_without_stale_csrf_token(self):
        handler = handler_with_headers(Host="127.0.0.1:8765", Origin="http://127.0.0.1:8765")
        with patch.object(server, "TEAM_MODE", False):
            handler.require_csrf({"csrf_token": "new-server-token"})

    def test_local_mode_rejects_cross_site_origin(self):
        handler = handler_with_headers(Host="127.0.0.1:8765", Origin="https://attacker.example")
        with patch.object(server, "TEAM_MODE", False):
            with self.assertRaises(PermissionError):
                handler.require_csrf({"csrf_token": "token"})

    def test_team_mode_still_requires_exact_csrf_token(self):
        handler = handler_with_headers(Host="127.0.0.1:8765", X_CSRF_Token="wrong")
        with patch.object(server, "TEAM_MODE", True):
            with self.assertRaises(PermissionError):
                handler.require_csrf({"csrf_token": "right"})


if __name__ == "__main__":
    unittest.main()
