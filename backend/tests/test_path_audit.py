from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUDIT_SCRIPT = PROJECT_ROOT / "scripts" / "check_active_paths.py"


def load_audit_module():
    if not AUDIT_SCRIPT.is_file():
        raise AssertionError("PP03 active path audit script is missing")
    spec = importlib.util.spec_from_file_location("check_active_paths", AUDIT_SCRIPT)
    if spec is None or spec.loader is None:
        raise AssertionError("PP03 active path audit script cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PathAuditTests(unittest.TestCase):
    def test_active_path_audit_detects_old_workspace_and_c_drive_paths(self) -> None:
        audit = load_audit_module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy_directory = "04" + "-Projects"
            (root / "run.ps1").write_text(
                "D:\\AI_Workspace\\" + legacy_directory + "\\PP03-AI-Jiyan\n"
                "C:" + "\\Users\\26365\\Documents\\ChatGPT\\PP03-old\n",
                encoding="utf-8",
            )

            findings = audit.find_forbidden_active_references(root)

        self.assertEqual(len(findings), 2)
        self.assertEqual(
            {finding.pattern for finding in findings},
            {"legacy_workspace", "legacy_c_drive"},
        )

    def test_active_path_audit_excludes_historical_evidence(self) -> None:
        audit = load_audit_module()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            legacy_directory = "04" + "-Projects"
            history = root / "docs" / "acceptance"
            history.mkdir(parents=True)
            (history / "closed.md").write_text(
                "D:\\AI_Workspace\\" + legacy_directory + "\\PP03-AI-Jiyan\n",
                encoding="utf-8",
            )

            findings = audit.find_forbidden_active_references(root)

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
