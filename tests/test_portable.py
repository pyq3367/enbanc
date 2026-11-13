"""便携性回归；可用 python -I -S -B tests/test_portable.py 无依赖运行。"""

from __future__ import annotations

import argparse
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from enbanc import doctor
from enbanc.cli import _factory, main
from enbanc.state import Run
from support import make_temp_dir, remove_tree


class PortableTests(unittest.TestCase):
    def setUp(self):
        tmp = make_temp_dir(doctor.temporary_base(), prefix="enbanc-portable-")
        self.addCleanup(remove_tree, tmp)
        self.tmp = tmp
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.dict(os.environ, {
            "ENBANC_TEST_TMP": str(self.tmp),
            "ENBANC_ROOT": str(self.tmp / "unused-runs"),
            "ENBANC_SKILL": str(self.tmp / "unused-private-skill"),
        }))
        for target in ("subprocess.run", "subprocess.Popen",
                       "socket.create_connection", "socket.socket.connect",
                       "enbanc.providers.CliProvider.complete"):
            stack.enter_context(patch(target, side_effect=AssertionError("禁止网络或真实账户调用")))
        read_text = Path.read_text

        def sandbox_read(path, *args, **kwargs):
            assert path.resolve().is_relative_to(self.tmp), "禁止读取沙箱外配置"
            return read_text(path, *args, **kwargs)

        stack.enter_context(patch.object(Path, "read_text", sandbox_read))

    def capture(self, fn, *args):
        stream = io.StringIO()
        with redirect_stdout(stream), redirect_stderr(stream):
            rc = fn(*args)
        return rc, stream.getvalue()

    def test_doctor_defaults_to_offline_without_cli_or_office(self):
        with patch.object(doctor.shutil, "which", return_value=None) as which:
            rc, output = self.capture(main, ["doctor"])
        self.assertEqual(rc, 0)
        self.assertEqual([call.args[0] for call in which.call_args_list], ["codebuddy", "cbc"])
        for status in ("package path: relative", "provider mock: available", "offline: available",
                       "CLI codebuddy: not-found (optional)", "CLI cbc: not-found (optional)",
                       "office: optional/not-checked", "desktop login: unverified",
                       "model permissions: unverified", "provider workbuddy: unsupported"):
            self.assertIn(status, output)
        self.assertNotIn(str(self.tmp), output)
        self.assertFalse((self.tmp / "unused-runs").exists())
        # doctor leaves its sandbox behind on purpose; deletion is a separate,
        # batched operation (tests/cleanup.py), not a side effect of a check.
        leftovers = [p for p in self.tmp.iterdir() if p.name.startswith("enbanc-doctor-")]
        self.assertTrue(leftovers)
        self.assertFalse((self.tmp / "unused-runs").exists())

    def test_cli_discovery_only_returns_command_status(self):
        variants = (None, "codebuddy.exe", "codebuddy.cmd", "cbc")
        for variant in variants:
            with self.subTest(variant=variant):
                location = str(self.tmp / variant) if variant else None
                with patch.object(doctor.shutil, "which", side_effect=[location, None]):
                    self.assertEqual(doctor.discover_cli(), {
                        "codebuddy": location is not None, "cbc": False,
                    })

    def test_found_cmd_is_never_executed_or_printed(self):
        location = str(self.tmp / "account.cmd")
        with patch.object(doctor.shutil, "which", side_effect=[location, None]):
            rc, output = self.capture(main, ["--root", str(self.tmp / "ignored"),
                                             "doctor", "--offline"])
        self.assertEqual(rc, 0)
        self.assertIn("CLI codebuddy: found", output)
        self.assertIn("desktop login: unverified", output)
        self.assertNotIn(location, output)
        self.assertNotIn(str(ROOT), output)
        self.assertTrue(any(p.name.startswith("enbanc-doctor-") for p in self.tmp.iterdir()))

    def test_resources_and_skill_entry_are_relative_and_optional(self):
        package = self.tmp / "package"
        package.mkdir()
        for name in doctor._PACKAGE_FILES:
            path = package / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        with patch.object(doctor, "files", return_value=package), \
             patch.object(doctor, "_source_root", return_value=self.tmp), \
             patch.object(doctor, "mock_smoke", return_value=True), \
             patch.object(doctor.shutil, "which", return_value=None):
            rc, output = self.capture(main, ["doctor"])
            self.assertEqual(rc, 0)
            self.assertIn("package resources: found", output)
            self.assertIn("skill entry: not-found (optional)", output)
            (self.tmp / "SKILL.md").touch()
            self.assertIn("skill entry: found", self.capture(main, ["doctor"])[1])
            # Rename rather than delete: the host limits delete operations.
            (package / "cli.py").rename(package / "cli.py.moved")
            rc, output = self.capture(main, ["doctor"])
            self.assertEqual(rc, 1)
            self.assertIn("package resources: missing", output)
            self.assertNotIn(str(package), output)

    def test_smoke_failure_does_not_leak_exception_or_environment(self):
        marker = "异常正文不得输出"
        with patch.object(doctor, "mock_smoke", side_effect=OSError(str(self.tmp) + marker)), \
             patch.object(doctor.shutil, "which", return_value=None):
            rc, output = self.capture(main, ["doctor", "--offline"])
        self.assertEqual(rc, 1)
        self.assertIn("provider mock: failed", output)
        self.assertNotIn(marker, output)
        self.assertNotIn(str(self.tmp), output)

    def test_unsupported_python_is_reported(self):
        with patch.object(doctor.sys, "version_info", (3, 9)), \
             patch.object(doctor.sys, "version", "3.9.0"), \
             patch.object(doctor, "mock_smoke", return_value=True), \
             patch.object(doctor.shutil, "which", return_value=None):
            rc, output = self.capture(main, ["doctor"])
        self.assertEqual(rc, 1)
        self.assertIn("Python 3.9.0 >=3.10: unsupported", output)

    def test_temporary_environment_precedence_and_system_fallback(self):
        names = ("ENBANC_TEST_TMP", "TMPDIR", "TEMP")
        paths = [self.tmp / name.lower() for name in names]
        for path in paths:
            path.mkdir()
        for start in range(len(names)):
            env = {name: str(path) for name, path in zip(names[start:], paths[start:])}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(doctor.temporary_base(), paths[start].resolve())
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(doctor.tempfile, "gettempdir", return_value=str(self.tmp)):
            self.assertEqual(doctor.temporary_base(), self.tmp.resolve())

    def test_invalid_temp_does_not_fall_back_or_write_in_project(self):
        for base in (str(ROOT), str(ROOT / "tests"), str(self.tmp / "missing"), "relative"):
            with self.subTest(base=base), patch.dict(os.environ, {"ENBANC_TEST_TMP": base}), \
                 patch.object(doctor.tempfile, "gettempdir", side_effect=AssertionError("禁止回退")):
                with self.assertRaises(ValueError):
                    doctor.temporary_base()
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_account_provider_is_rejected_before_loading_runs_or_models(self):
        for command in ("dispatch", "review"):
            argv = [command, "--run-id", "absent", "--provider", "workbuddy"]
            if command == "dispatch":
                argv += ["--tasks", str(self.tmp / "missing.json")]
            with self.subTest(command=command), patch.object(Run, "load", side_effect=AssertionError("禁止加载 run")):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
                    main(argv)
                self.assertEqual(exc.exception.code, 2)
        with self.assertRaises(SystemExit) as exc:
            _factory(argparse.Namespace(provider="workbuddy"))
        self.assertIn("未调用模型", str(exc.exception))
        self.assertEqual(list(self.tmp.iterdir()), [])

    def test_standard_library_demo_passes_and_refuses_incomplete_delivery(self):
        spec = importlib.util.spec_from_file_location("portable_demo", ROOT / "examples/demo/run.py")
        demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(demo)
        demo.ROOT = self.tmp / "demo-runs"
        _, output = self.capture(demo.scenario, "demo-pass", 1.0)
        self.assertIn("delivered: completed", output)
        _, output = self.capture(demo.scenario, "demo-fail", 0.3)
        self.assertIn("refused:", output)
        delivered = Run.load("demo-pass", demo.ROOT).state
        self.assertEqual(delivered["outcome"], "completed")
        # The demo is a single-artifact run, so delivery goes through the
        # recorded waiver; a silent pass would be the bug this gate exists for.
        self.assertEqual(delivered["solo_waiver"]["stage"], "P5")
        self.assertTrue(delivered["solo_waiver"]["reason"].strip())
        self.assertNotEqual(Run.load("demo-fail", demo.ROOT).state["outcome"], "completed")
        self.assertTrue(demo.ROOT.resolve().is_relative_to(self.tmp))


if __name__ == "__main__":
    unittest.main(verbosity=2)
