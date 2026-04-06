"""user_config.py 테스트"""
from __future__ import annotations

import os
import tempfile
import unittest
from types import SimpleNamespace

from user_config import load_user_config, save_user_config, init_user_config, apply_defaults


class TestUserConfig(unittest.TestCase):
    def test_load_missing_file(self):
        result = load_user_config("/nonexistent/path.yaml")
        self.assertEqual(result, {})

    def test_save_and_load(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name
        try:
            save_user_config({"default_host": "10.0.0.1"}, path)
            loaded = load_user_config(path)
            self.assertEqual(loaded["default_host"], "10.0.0.1")
        finally:
            os.unlink(path)

    def test_init_creates_file(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False) as f:
            path = f.name
        os.unlink(path)  # 삭제 후 init
        try:
            result = init_user_config(path)
            self.assertEqual(result, path)
            self.assertTrue(os.path.exists(path))
            loaded = load_user_config(path)
            self.assertIn("default_host", loaded)
        finally:
            os.unlink(path)

    def test_init_existing_no_overwrite(self):
        with tempfile.NamedTemporaryFile(suffix=".yaml", delete=False, mode="w") as f:
            f.write("custom: true\n")
            path = f.name
        try:
            init_user_config(path)
            loaded = load_user_config(path)
            self.assertTrue(loaded.get("custom"))
        finally:
            os.unlink(path)

    def test_apply_defaults_cli_priority(self):
        args = SimpleNamespace(host="cli-host", user=None, password=None, webhook=None)
        config = {"default_host": "config-host", "default_user": "admin"}
        apply_defaults(args, config)
        self.assertEqual(args.host, "cli-host")  # CLI 우선
        self.assertEqual(args.user, "admin")  # config 적용

    def test_apply_defaults_empty_config(self):
        args = SimpleNamespace(host=None, user=None, password=None, webhook=None)
        apply_defaults(args, {})
        self.assertIsNone(args.host)


if __name__ == "__main__":
    unittest.main()
