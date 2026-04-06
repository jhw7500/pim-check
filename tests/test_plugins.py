"""checks/plugins 자동 로드 테스트"""
from __future__ import annotations

import os
import tempfile
import unittest

from checks import load_plugins


class TestLoadPlugins(unittest.TestCase):
    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = load_plugins(tmpdir)
            self.assertEqual(result, [])

    def test_nonexistent_dir(self):
        result = load_plugins("/nonexistent/plugins")
        self.assertEqual(result, [])

    def test_ignores_underscore_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "_hidden.py"), "w") as f:
                f.write("class Foo: pass\n")
            result = load_plugins(tmpdir)
            self.assertEqual(result, [])

    def test_loads_valid_plugin(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plugin_code = '''
from checks.base_check import BaseCheck

class UptimeCheck(BaseCheck):
    name = "uptime"
    def collect(self, ssh, config):
        return {"uptime": ssh.run("uptime")}
    def validate(self, data, config):
        return True, "OK"
'''
            with open(os.path.join(tmpdir, "uptime_check.py"), "w") as f:
                f.write(plugin_code)
            result = load_plugins(tmpdir)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0].name, "uptime")

    def test_skips_non_basecheck(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "notacheck.py"), "w") as f:
                f.write("class Foo:\n    pass\n")
            result = load_plugins(tmpdir)
            self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
