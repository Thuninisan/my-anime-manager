"""Offline update checks using disposable Git repositories."""
import ast
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "update_check", ROOT / "backend/services/update_check.py")
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.remote = Path(self.tmp.name) / "remote"
        self.local = Path(self.tmp.name) / "local"
        self.git("init", "-b", "release", str(self.remote))
        self.git("-C", str(self.remote), "config", "user.name", "Test")
        self.git("-C", str(self.remote), "config", "user.email", "test@example.com")
        self.commit()
        self.git("clone", "--depth=1", "--branch", "release",
                 self.remote.as_uri(), str(self.local))

    def git(self, *args):
        return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.git("-C", str(self.remote), "commit", "--allow-empty", "-m", "test")

    def test_shallow_clone_detects_new_push_on_configured_branch(self):
        self.assertFalse(service.inspect_remote(str(self.local), "release")["update_available"])
        before = self.git("-C", str(self.local), "rev-parse", "HEAD")
        self.commit()
        result = service.inspect_remote(str(self.local), "release")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["commits_behind"], 1)
        self.assertNotEqual(result["current_sha"], result["latest_sha"])
        self.assertEqual(before, self.git("-C", str(self.local), "rev-parse", "HEAD"))

    def test_missing_branch_reports_error(self):
        self.assertIn("error", service.inspect_remote(str(self.local), "missing"))

    def test_failed_comparison_reports_error(self):
        ok = subprocess.CompletedProcess([], 0, "abc\n", "")
        failed = subprocess.CompletedProcess([], 128, "", "bad revision")
        with patch.object(service.subprocess, "run", side_effect=[ok, ok, ok, failed]):
            self.assertIn("bad revision", service.inspect_remote(str(self.local), "release")["error"])

    def test_timeout_reports_error(self):
        with patch.object(service.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 30)):
            self.assertIn("error", service.inspect_remote(str(self.local), "release"))


class CacheTests(unittest.TestCase):
    def setUp(self):
        # Load the route function without importing application workers/clients.
        path = ROOT / "backend/api/routes_system.py"
        tree = ast.parse(path.read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "check_update")
        self.assertEqual(ast.literal_eval(fn.args.defaults[0]), False)
        fn.decorator_list = []
        self.state = types.SimpleNamespace(_update_cache={"checked_at": None, "result": None})
        self.ns = dict(os=os, time=time, state=self.state, __version__="test")
        exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), "exec"), self.ns)
        self.check = self.ns["check_update"]

    def test_force_bypasses_fresh_cache(self):
        calls = []
        def inspect(*args):
            calls.append(args)
            return {"update_available": len(calls) > 1}
        self.ns["inspect_remote"] = inspect
        self.assertFalse(self.check()["update_available"])
        self.assertFalse(self.check()["update_available"])
        self.assertTrue(self.check(force=True)["update_available"])
        self.assertEqual(len(calls), 2)

    def test_error_invalidates_stale_success(self):
        self.ns["inspect_remote"] = lambda *args: {"update_available": False}
        self.check()
        self.ns["inspect_remote"] = lambda *args: {"error": "fetch failed"}
        self.assertIn("error", self.check(force=True))
        self.assertIsNone(self.state._update_cache["result"])

    def test_branch_change_bypasses_cache(self):
        calls = []
        self.ns["inspect_remote"] = lambda *args: calls.append(args) or {"update_available": False}
        with patch.dict(os.environ, {"MAM_BRANCH": "main"}):
            self.check()
        with patch.dict(os.environ, {"MAM_BRANCH": "release"}):
            self.check()
        self.assertEqual([args[1] for args in calls], ["main", "release"])


if __name__ == "__main__":
    unittest.main()
