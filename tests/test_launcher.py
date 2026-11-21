from __future__ import annotations

import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

# Windows recognizes .pyw as an importable suffix; Linux needs an explicit loader.
loader = SourceFileLoader("spark_launcher", str(Path(__file__).resolve().parents[1] / "spark_launcher.pyw"))
launcher = module_from_spec(spec_from_loader(loader.name, loader))
loader.exec_module(launcher)


class UpdateHttpsOriginTests(unittest.TestCase):
    def test_replaces_existing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("SPARK_TEAM_MODE=1\n"
                           "SPARK_TEAM_HTTPS_ORIGIN=https://old.trycloudflare.com\n",
                           encoding="utf-8")
            launcher.update_https_origin("https://new.trycloudflare.com", env_path=env)
            lines = env.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[0], "SPARK_TEAM_MODE=1")
            self.assertEqual(len([l for l in lines if l.startswith("SPARK_TEAM_HTTPS_ORIGIN")]), 1)
            self.assertEqual(lines[1], "SPARK_TEAM_HTTPS_ORIGIN=https://new.trycloudflare.com")

    def test_strips_trailing_slash_and_appends_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            env.write_text("SPARK_TEAM_MODE=1\n", encoding="utf-8")
            launcher.update_https_origin("https://a.trycloudflare.com/", env_path=env)
            self.assertEqual(env.read_text(encoding="utf-8").splitlines()[-1],
                             "SPARK_TEAM_HTTPS_ORIGIN=https://a.trycloudflare.com")

    def test_creates_file_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / ".env"
            launcher.update_https_origin("https://b.trycloudflare.com", env_path=env)
            self.assertEqual(env.read_text(encoding="utf-8"),
                             "SPARK_TEAM_HTTPS_ORIGIN=https://b.trycloudflare.com\n")


if __name__ == "__main__":
    unittest.main()
