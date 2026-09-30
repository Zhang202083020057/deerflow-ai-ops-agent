"""End-to-end checks for both incident paths, using only local subprocesses."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "skill" / "ai-ops-triage" / "scripts"
CLI = SCRIPTS / "ops_lab.py"
SERVICE = SCRIPTS / "lab_service.py"


@contextmanager
def work_directory():
    base = REPO / ".test-work"
    base.mkdir(exist_ok=True)
    root = base / uuid.uuid4().hex
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root)


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        timeout=10,
    )


class LabTests(unittest.TestCase):
    def run_case(self, case: str, action: str, expected_reason: str) -> None:
        with work_directory() as temp:
            root = temp / "incident"
            created = json.loads(command("create", "--root", str(root), "--case", case).stdout)
            service = subprocess.Popen(
                [sys.executable, str(SERVICE), "--root", str(root)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
            )
            try:
                for _ in range(40):
                    if service.poll() is not None:
                        self.fail(f"Lab service exited early: {service.stderr.read()}")
                    try:
                        with urlopen(created["app_url"] + "/_live", timeout=0.2):
                            break
                    except (URLError, TimeoutError, OSError):
                        time.sleep(0.1)
                else:
                    self.fail("Lab service did not start")

                before = json.loads(command("inspect", "--root", str(root)).stdout)
                self.assertEqual(before["application"]["status"], 503)
                self.assertEqual(before["application"]["body"]["reason"], expected_reason)
                self.assertEqual(before["inventory_model"]["status"], 200)
                self.assertTrue(before["recent_logs"])
                if case == "model_unreachable":
                    self.assertFalse(before["configured_model"]["reachable"])
                else:
                    self.assertEqual(before["configured_model"]["status"], 200)

                applied = json.loads(command("apply", "--root", str(root), "--action", action).stdout)
                self.assertEqual(applied["action"], action)
                after = json.loads(command("inspect", "--root", str(root)).stdout)
                self.assertEqual(after["application"]["status"], 200)
                self.assertEqual(after["application"]["body"]["reason"], "ok")
                self.assertEqual(after["config"][applied["field"]], after["inventory"][applied["field"]])
                self.assertEqual(len((root / "actions.jsonl").read_text(encoding="utf-8").splitlines()), 1)
            finally:
                service.terminate()
                try:
                    service.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    service.kill()
                    service.wait(timeout=5)
                service.stderr.close()

    def test_unreachable_model_url(self) -> None:
        self.run_case("model_unreachable", "fix_model_url", "model_endpoint_unreachable")

    def test_wrong_model_id(self) -> None:
        self.run_case("wrong_model_id", "fix_model_id", "model_not_found")

    def test_refuses_non_lab_and_nonempty_target(self) -> None:
        with work_directory() as root:
            self.assertNotEqual(command("inspect", "--root", str(root), check=False).returncode, 0)
            (root / "keep.txt").write_text("keep", encoding="utf-8")
            self.assertNotEqual(
                command("create", "--root", str(root), "--case", "wrong_model_id", check=False).returncode,
                0,
            )
            self.assertEqual((root / "keep.txt").read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
