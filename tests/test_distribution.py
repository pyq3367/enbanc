"""Portable public skill and explicit privacy choices; synthetic data only."""
from __future__ import annotations

import importlib.util
import os
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from enbanc.providers.cli import child_environment

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("enbanc_distribution_gate", ROOT / ".sync/publish.py")
PUB = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PUB)


def test_public_contact_only_in_approved_locations():
    contact = PUB.PUBLIC_CONTACT
    for name in PUB.CONTACT_FILES:
        assert PUB.scan_bytes(name, contact.encode()) == []
    # Git records the author in the commit object, so it is expected there.
    for name in ("[历史提交]", "[历史标签]"):
        assert PUB.scan_bytes(name, contact.encode()) == []
    assert PUB.scan_bytes("src/enbanc/a.py", contact.encode())
    other = "somebody" + "@" + "gmail" + ".com"
    result = PUB.scan_bytes("README.md", other.encode())
    assert result and all(other not in line for line in result)
    assert PUB.scan_bytes("docs/example.md", b"fixture@example.com") == []
    assert "@" not in PUB.diagnostic("docs/" + other + ".md", 1, "fixture")


def test_environment_does_not_implicitly_grant_secrets():
    value = "sensitive-value"
    with patch.dict(os.environ, {"PATH": "approved-bin", "UNRELATED_SECRET": value,
                                 "AWS_ACCESS_KEY_ID": value, "HTTP_PROXY": value}, clear=True):
        env = child_environment()
        assert env == {"PATH": "approved-bin"}
        assert child_environment({"APP_TOKEN": "explicit-fixture"})["APP_TOKEN"] == "explicit-fixture"


def test_public_skill_valid_and_self_contained():
    assert PUB.validate_skill_entry(ROOT) == []
    assert PUB.scan_bytes("SKILL.md", (ROOT / "SKILL.md").read_bytes()) == []
    assert PUB.scan_bytes("references/safety-rules.md", (ROOT / "references/safety-rules.md").read_bytes()) == []


def test_export_includes_only_public_entry_and_rules(tmp_path):
    (tmp_path / "references").mkdir()
    for name in ("SKILL.md", "references/safety-rules.md", "LICENSE"):
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())
    (tmp_path / "private.txt").write_text("private fixture", encoding="utf-8")
    with patch.object(PUB, "REPO", tmp_path), patch.object(PUB, "cmd_check", return_value=0):
        assert PUB.cmd_export_skill(SimpleNamespace()) == 0
        assert PUB.cmd_export_skill(SimpleNamespace()) == 2
    with zipfile.ZipFile(tmp_path / "dist/enbanc-skill.zip") as archive:
        assert set(archive.namelist()) == {"enbanc/SKILL.md", "enbanc/references/safety-rules.md", "enbanc/LICENSE"}
        assert archive.testzip() is None


def test_export_is_blocked_by_failed_check(tmp_path):
    with patch.object(PUB, "REPO", tmp_path), patch.object(PUB, "cmd_check", return_value=1):
        assert PUB.cmd_export_skill(SimpleNamespace()) == 1
    assert not (tmp_path / "dist").exists()
