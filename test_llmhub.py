"""Unit tests for llmhub.py — no network, no keyring, no opencode needed."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add parent dir so we can import llmhub regardless of where tests run from
sys.path.insert(0, str(Path(__file__).parent))
import llmhub


class TestConfigPath(unittest.TestCase):
    def test_env_override(self):
        with patch.dict(os.environ, {"OPENCODE_CONFIG": "/custom/opencode.json"}):
            self.assertEqual(llmhub._config_path(), Path("/custom/opencode.json"))

    def test_windows_default(self):
        with patch.object(sys, "platform", "win32"):
            with patch.dict(os.environ, {"USERPROFILE": "C:/Users/test"}, clear=False):
                p = llmhub._config_path()
                self.assertIn(".config", str(p))
                self.assertIn("opencode", str(p))

    def test_linux_default(self):
        with patch.object(sys, "platform", "linux"):
            with patch.dict(os.environ, {}, clear=False):
                p = llmhub._config_path()
                self.assertIn("opencode", str(p))


class TestGetKey(unittest.TestCase):
    def test_from_env(self):
        with patch.dict(os.environ, {"NVIDIA_API_KEY": "nvkey123"}):
            self.assertEqual(llmhub._get_key("NVIDIA_API_KEY"), "nvkey123")

    def test_missing_no_secret_py(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.object(Path, "exists", return_value=False):
                self.assertIsNone(llmhub._get_key("NVIDIA_API_KEY"))


class TestBuildProviderEntry(unittest.TestCase):
    def setUp(self):
        self.info = {
            "base_url": "https://integrate.api.nvidia.com/v1",
            "key_env": "NVIDIA_API_KEY",
            "models": {
                "nvidia/nemotron-ultra-253b-instruct": {"name": "Nemotron Ultra", "class": "OPUS"},
                "nvidia/nemotron-super-49b-instruct":  {"name": "Nemotron Super", "class": "SONNET"},
            },
        }

    def test_structure(self):
        entry = llmhub._build_provider_entry("nvidia", self.info, "mykey")
        self.assertEqual(entry["name"], "NVIDIA")
        self.assertEqual(entry["package"], "@opencode/ai/providers/openai-compatible")
        self.assertEqual(entry["settings"]["baseURL"], "https://integrate.api.nvidia.com/v1")
        self.assertEqual(entry["settings"]["apiKey"], "mykey")
        self.assertIn("nvidia/nemotron-ultra-253b-instruct", entry["models"])

    def test_no_key(self):
        entry = llmhub._build_provider_entry("nvidia", self.info, None)
        self.assertNotIn("apiKey", entry["settings"])

    def test_model_names(self):
        entry = llmhub._build_provider_entry("nvidia", self.info, "k")
        self.assertEqual(entry["models"]["nvidia/nemotron-ultra-253b-instruct"]["name"], "Nemotron Ultra")


class TestWriteConfig(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "sub" / "opencode.json"
            data = {"providers": {"nvidia": {"name": "NVIDIA"}}}
            llmhub._write_config(p, data)
            self.assertTrue(p.exists())
            loaded = json.loads(p.read_text())
            self.assertEqual(loaded["providers"]["nvidia"]["name"], "NVIDIA")

    def test_backup_created(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "opencode.json"
            p.write_text('{"old": true}')
            llmhub._write_config(p, {"new": True})
            backups = list(Path(td).glob("*.bak-*"))
            self.assertEqual(len(backups), 1)


class TestMainDryRun(unittest.TestCase):
    def test_dry_run_no_write(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "opencode.json"
            with patch.dict(os.environ, {
                "OPENCODE_CONFIG": str(cfg),
                "NVIDIA_API_KEY": "nvkey",
                "GEMINI_API_KEY": "gmkey",
                "OPENAI_API_KEY": "oaikey",
            }):
                rc = llmhub.main(["--dry-run"])
            self.assertEqual(rc, 0)
            self.assertFalse(cfg.exists())

    def test_list_returns_zero(self):
        with patch.dict(os.environ, {}):
            rc = llmhub.main(["--list"])
        self.assertEqual(rc, 0)

    def test_no_keys_returns_one(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = Path(td) / "opencode.json"
            with patch.dict(os.environ, {"OPENCODE_CONFIG": str(cfg)}, clear=True):
                with patch("llmhub.Path.exists", return_value=False):
                    rc = llmhub.main([])
        self.assertEqual(rc, 1)


class TestModelsJson(unittest.TestCase):
    """Verify models.json is valid and has required fields."""
    def setUp(self):
        models_path = Path(__file__).parent / "models.json"
        self.data = json.loads(models_path.read_text())

    def test_providers_present(self):
        for prov in ("nvidia", "gemini", "openai"):
            self.assertIn(prov, self.data)

    def test_required_keys(self):
        for prov, info in self.data.items():
            for key in ("base_url", "key_env", "models"):
                self.assertIn(key, info, f"{prov} missing {key}")

    def test_model_entries(self):
        for prov, info in self.data.items():
            self.assertGreater(len(info["models"]), 0, f"{prov} has no models")
            for mid, meta in info["models"].items():
                self.assertIn("name", meta, f"{mid} missing name")
                self.assertIn("class", meta, f"{mid} missing class")
                self.assertIn(meta["class"], ("OPUS", "SONNET", "HAIKU", "FABLE"),
                              f"{mid} has unknown class {meta['class']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
