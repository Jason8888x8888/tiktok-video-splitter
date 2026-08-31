#!/usr/bin/env python3
"""Verify the repository is a clean, self-contained public Skill package."""

from __future__ import annotations

import json
import os
import py_compile
import re
import subprocess
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SKILL_ROOT = REPOSITORY_ROOT
REQUIRED_REPOSITORY_FILES = {
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "README.md",
    "SECURITY.md",
    "requirements.txt",
}
REQUIRED_SKILL_FILES = {
    "SKILL.md",
    "agents/interface.yaml",
    "agents/openai.yaml",
    "references/shot-label-prompt.md",
    "requirements.txt",
    "security/network_policy.json",
    "security/permission_policy.json",
    "scripts/assetize_tiktok.py",
    "scripts/download_tiktok.py",
    "scripts/package_contracts.py",
    "scripts/preflight_checks.py",
    "scripts/runtime_safety.py",
    "tests/test_assetize_tiktok.py",
}
FORBIDDEN_NAMES = {"__pycache__"}
PLACEHOLDER_PATTERNS = (
    re.compile(r"\bTODO\b"),
    re.compile(r"your[-_ ](?:org|repo|username)", re.IGNORECASE),
)


def fail(message: str) -> None:
    raise RuntimeError(message)


def verify_required_files() -> None:
    for relative in sorted(REQUIRED_REPOSITORY_FILES):
        if not (REPOSITORY_ROOT / relative).is_file():
            fail(f"Missing repository file: {relative}")
    for relative in sorted(REQUIRED_SKILL_FILES):
        if not (SKILL_ROOT / relative).is_file():
            fail(f"Missing Skill file: {relative}")


def verify_clean_tree() -> None:
    ignored_patterns = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")
    if ".DS_Store" not in ignored_patterns:
        fail(".gitignore must exclude .DS_Store")
    for path in REPOSITORY_ROOT.rglob("*"):
        if path.name in FORBIDDEN_NAMES or path.suffix == ".pyc":
            fail(f"Generated file must not be published: {path.relative_to(REPOSITORY_ROOT)}")


def verify_skill_frontmatter() -> None:
    content = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    if not content.startswith("---\n"):
        fail("SKILL.md is missing YAML frontmatter")
    try:
        frontmatter = content.split("---\n", 2)[1]
    except IndexError as exc:
        raise RuntimeError("SKILL.md frontmatter is not closed") from exc
    if not re.search(r"(?m)^name:\s+tiktok-video-splitter\s*$", frontmatter):
        fail("SKILL.md name is missing or invalid")
    if not re.search(r"(?m)^description:\s*\|\s*$", frontmatter):
        fail("SKILL.md description must use a YAML block scalar")


def verify_public_text() -> None:
    for path in REPOSITORY_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in {
            ".md",
            ".py",
            ".yaml",
            ".yml",
            ".json",
            ".txt",
        }:
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        content = path.read_text(encoding="utf-8")
        if "/Users/" in content or "C:\\Users\\" in content:
            fail(f"Private absolute path found in {path.relative_to(REPOSITORY_ROOT)}")
        if path.name in {"README.md", "SKILL.md"}:
            for pattern in PLACEHOLDER_PATTERNS:
                if pattern.search(content):
                    fail(f"Placeholder text found in {path.relative_to(REPOSITORY_ROOT)}")


def verify_json_schemas() -> None:
    schema_dir = SKILL_ROOT / "references" / "schemas"
    schemas = sorted(schema_dir.glob("*.schema.json"))
    if len(schemas) < 4:
        fail("Expected schemas for candidates, download summary, segments, and run summary")
    for path in schemas:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            fail(f"Unexpected JSON Schema draft: {path.name}")


def verify_python() -> None:
    with tempfile.TemporaryDirectory(prefix="tiktok-splitter-pycompile-") as temp_dir:
        temporary = Path(temp_dir)
        for index, path in enumerate(sorted((SKILL_ROOT / "scripts").glob("*.py"))):
            py_compile.compile(
                str(path),
                cfile=str(temporary / f"{index}.pyc"),
                doraise=True,
            )


def run_tests() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(SKILL_ROOT / "tests"),
            "-v",
        ],
        cwd=REPOSITORY_ROOT,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if completed.returncode != 0:
        fail("Unit tests failed")


def main() -> int:
    verify_required_files()
    verify_clean_tree()
    verify_skill_frontmatter()
    verify_public_text()
    verify_json_schemas()
    verify_python()
    run_tests()
    verify_clean_tree()
    print("Release verification passed.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, json.JSONDecodeError, py_compile.PyCompileError) as exc:
        raise SystemExit(f"Release verification failed: {exc}") from exc
