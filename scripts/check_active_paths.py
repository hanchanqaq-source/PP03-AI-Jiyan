from __future__ import annotations

import argparse
from pathlib import Path
from typing import NamedTuple


class Finding(NamedTuple):
    path: Path
    line: int
    pattern: str


_EXCLUDED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    "dist",
    ".tmp",
}
_EXCLUDED_SEQUENCES = {
    ("docs", "acceptance"),
    ("docs", "migrations"),
    ("docs", "screenshots"),
    ("docs", "superpowers"),
}
_TEXT_SUFFIXES = {
    "",
    ".cmd",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}


def _is_excluded(relative: Path) -> bool:
    parts = tuple(part.casefold() for part in relative.parts)
    if any(part in _EXCLUDED_PARTS for part in parts):
        return True
    return any(
        parts[index : index + len(sequence)] == sequence
        for sequence in _EXCLUDED_SEQUENCES
        for index in range(len(parts) - len(sequence) + 1)
    )


def _patterns() -> tuple[tuple[str, str], ...]:
    legacy_directory = "04" + "-Projects"
    return (
        ("legacy_workspace", "d:\\ai_workspace\\" + legacy_directory.casefold()),
        ("legacy_project_segment", "\\" + legacy_directory.casefold() + "\\"),
        (
            "legacy_c_drive",
            "c:\\users\\26365\\documents\\chatgpt\\" + "pp03",
        ),
    )


def find_forbidden_active_references(root: Path) -> list[Finding]:
    root = Path(root).resolve()
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if _is_excluded(relative) or path.suffix.casefold() not in _TEXT_SUFFIXES:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            continue
        for number, line in enumerate(lines, start=1):
            normalized = line.casefold().replace("/", "\\")
            for name, pattern in _patterns():
                if pattern in normalized:
                    findings.append(Finding(relative, number, name))
                    break
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject active references to retired PP03 paths")
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    findings = find_forbidden_active_references(args.root)
    for finding in findings:
        print(f"{finding.path}:{finding.line}:{finding.pattern}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
