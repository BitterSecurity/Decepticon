"""Real UID boundary checks; run this file as root on Linux."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(hasattr(os, "fork") and os.geteuid() == 0, "requires Linux root and UID1500")
class PublishedScopeRootTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="managed-scope-root-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.base.chmod(0o755)
        self.workspace = self.base / "workspace"
        self.workspace.mkdir()
        self.workspace.chmod(0o1777)
        self.plan = self.workspace / "plan"
        self.plan.mkdir()
        self.plan.chmod(0o1777)
        self.content = b'{"machine_enforcement":{"in_scope":["example.com"]}}'
        self.scope = self.plan / "roe.json"
        self.scope.write_bytes(self.content)
        self.scope.chmod(0o444)
        source = Path(__file__).resolve().parents[3] / "decepticon_sandbox/server/managed_start.py"
        spec = importlib.util.spec_from_file_location("root_scope_fixture", source)
        assert spec is not None and spec.loader is not None
        self.managed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.managed)

    def run_as_workload(self, content, *, probe_protection=False):
        reader, writer = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(reader)
            result: dict[str, object]
            try:
                os.setgroups([])
                os.setgid(1500)
                os.setuid(1500)
                self.managed.restore_scope(self.workspace, content)
                result = {"ok": True, "uid": os.geteuid()}
                if probe_protection:
                    for name, operation in (
                        ("unlink", self.scope.unlink),
                        ("chmod", lambda: self.scope.chmod(0o600)),
                    ):
                        try:
                            operation()
                        except PermissionError:
                            result[name + "Denied"] = True
                        else:
                            result[name + "Denied"] = False
            except Exception as error:
                result = {
                    "ok": False,
                    "error": type(error).__name__,
                    "errno": getattr(error, "errno", None),
                }
            os.write(writer, json.dumps(result).encode())
            os.close(writer)
            os._exit(0)
        os.close(writer)
        with os.fdopen(reader, "rb") as output:
            raw = output.read(4097)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(status, 0)
        self.assertLessEqual(len(raw), 4096)
        return json.loads(raw)

    def test_matching_scope_preserves_root_inode_and_denies_workload_mutation(self):
        before = self.scope.stat()
        result = self.run_as_workload(self.content, probe_protection=True)
        self.assertEqual(
            result, {"ok": True, "uid": 1500, "unlinkDenied": True, "chmodDenied": True}
        )
        after = self.scope.stat()
        self.assertEqual(
            (after.st_ino, after.st_uid, stat.S_IMODE(after.st_mode)), (before.st_ino, 0, 0o444)
        )
        self.assertEqual(
            (after.st_mtime_ns, after.st_ctime_ns), (before.st_mtime_ns, before.st_ctime_ns)
        )
        self.assertEqual(self.scope.read_bytes(), self.content)
        self.assertEqual(list(self.plan.iterdir()), [self.scope])

    def test_different_approved_bytes_cannot_adopt_or_replace_the_root_file(self):
        before = self.scope.stat().st_ino
        result = self.run_as_workload(b'{"machine_enforcement":{"in_scope":["example.org"]}}')
        self.assertFalse(result["ok"])
        self.assertEqual(self.scope.stat().st_ino, before)
        self.assertEqual(self.scope.read_bytes(), self.content)

    def test_writable_root_context_is_not_adopted(self):
        self.scope.chmod(0o644)
        self.assertFalse(self.run_as_workload(self.content)["ok"])
        self.assertEqual(self.scope.read_bytes(), self.content)

    def test_another_workload_owners_file_is_not_adopted(self):
        os.chown(self.scope, 1501, 1501)
        self.assertFalse(self.run_as_workload(self.content)["ok"])
        self.assertEqual(self.scope.stat().st_uid, 1501)

    def test_hardlinked_root_context_is_not_adopted(self):
        other = self.base / "other.json"
        os.link(self.scope, other)
        self.assertFalse(self.run_as_workload(self.content)["ok"])
        self.assertEqual(self.scope.stat().st_nlink, 2)
        self.assertEqual(other.read_bytes(), self.content)

    def test_scope_symlink_is_not_followed_or_replaced(self):
        outside = self.base / "outside.json"
        outside.write_bytes(self.content)
        self.scope.unlink()
        self.scope.symlink_to(outside)
        self.assertFalse(self.run_as_workload(self.content)["ok"])
        self.assertTrue(self.scope.is_symlink())
        self.assertEqual(outside.read_bytes(), self.content)

    def test_same_bytes_path_replacement_during_verification_is_refused(self):
        replacement = self.plan / "replacement"
        replacement.write_bytes(self.content)
        replacement.chmod(0o444)
        original = os.stat
        switched = False
        observations = 0

        def observe(name, *args, **kwargs):
            nonlocal switched, observations
            if name == "roe.json" and kwargs.get("dir_fd") is not None:
                observations += 1
                if observations == 2:
                    switched = True
                    os.replace(replacement, "roe.json", dst_dir_fd=kwargs["dir_fd"])
            return original(name, *args, **kwargs)

        with patch.object(self.managed.os, "stat", observe):
            with self.assertRaisesRegex(ValueError, "changed during verification"):
                self.managed.restore_scope(self.workspace, self.content)
        self.assertTrue(switched)


if __name__ == "__main__":
    unittest.main()
