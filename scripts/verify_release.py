#!/usr/bin/env python3
"""Verify the repository is a clean, self-contained public Skill package."""

from __future__ import annotations

import argparse
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
    ".github/ISSUE_TEMPLATE/bug_report.yml",
    ".github/ISSUE_TEMPLATE/config.yml",
    ".github/ISSUE_TEMPLATE/feature_request.yml",
    ".github/pull_request_template.md",
    ".github/workflows/ci.yml",
    ".gitignore",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "docs/assets/output-directory-example.png",
    "LICENSE",
    "manifest.json",
    "README.md",
    "SECURITY.md",
    "requirements.txt",
}
REQUIRED_SKILL_FILES = {
    "SKILL.md",
    "agents/interface.yaml",
    "agents/openai.yaml",
    "evals/history/2026-09-01-beta3.json",
    "evals/output/cases.jsonl",
    "evals/trigger_cases.json",
    "references/auto-tuning.md",
    "references/quality-gates.md",
    "references/schemas/candidates.schema.json",
    "references/schemas/download-summary.schema.json",
    "references/schemas/run-summary.schema.json",
    "references/schemas/segments.schema.json",
    "references/shot-label-prompt.md",
    "reports/conformance-agent-skills.json",
    "reports/conformance-generic.json",
    "reports/conformance-openai.json",
    "reports/output_quality_scorecard.json",
    "reports/output_quality_scorecard.md",
    "reports/security_trust_report.json",
    "reports/skill-ir.json",
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
        if ".git" in path.relative_to(REPOSITORY_ROOT).parts:
            continue
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
        if ".git" in path.relative_to(REPOSITORY_ROOT).parts:
            continue
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


def verify_release_metadata() -> None:
    manifest = json.loads((REPOSITORY_ROOT / "manifest.json").read_text(encoding="utf-8"))
    version = manifest.get("version")
    if not version:
        fail("manifest.json is missing version")
    contracts = (SKILL_ROOT / "scripts" / "package_contracts.py").read_text(
        encoding="utf-8"
    )
    if f'SKILL_VERSION = "{version}"' not in contracts:
        fail("manifest and package contract versions differ")
    for relative in ("README.md", "CHANGELOG.md"):
        if version not in (REPOSITORY_ROOT / relative).read_text(encoding="utf-8"):
            fail(f"{relative} does not mention release version {version}")
    for relative in (
        "reports/conformance-agent-skills.json",
        "reports/conformance-generic.json",
        "reports/conformance-openai.json",
        "reports/security_trust_report.json",
    ):
        payload = json.loads((REPOSITORY_ROOT / relative).read_text(encoding="utf-8"))
        if payload.get("ok") is not True:
            fail(f"Release evidence did not pass: {relative}")


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


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="Verify the repository's public release package."
    )


def main() -> int:
    verify_required_files()
    verify_clean_tree()
    verify_skill_frontmatter()
    verify_public_text()
    verify_json_schemas()
    verify_release_metadata()
    verify_python()
    run_tests()
    verify_clean_tree()
    print("Release verification passed.")
    return 0


def cli() -> int:
    build_parser().parse_args()
    return main()


if __name__ == "__main__":
    try:
        raise SystemExit(cli())
    except (OSError, RuntimeError, json.JSONDecodeError, py_compile.PyCompileError) as exc:
        raise SystemExit(f"Release verification failed: {exc}") from exc
