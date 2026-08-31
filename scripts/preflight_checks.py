#!/usr/bin/env python3
"""Read-only capability checks for the TikTok video splitter CLI."""

from __future__ import annotations

import os
import json
import shutil
from pathlib import Path
from typing import Callable

from package_contracts import nearest_existing_parent, normalize_and_validate_segments
from runtime_safety import resolve_package_member


SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Imported by the main CLI to perform read-only capability checks."


def build_preflight_report(
    args,
    *,
    is_tiktok_input: Callable[[str], bool],
    selected_mode: Callable[[object], str],
    load_api_key: Callable[[Path | None], tuple[str, str]],
    probe_media: Callable[..., dict],
) -> dict:
    checks: list[dict] = []

    def add_check(name: str, required: bool, ok: bool, detail: str) -> None:
        checks.append(
            {"name": name, "required": required, "ok": ok, "detail": detail}
        )

    render_only = bool(args.render_only)
    input_is_url = bool(args.input and is_tiktok_input(args.input))
    mode = selected_mode(args)
    resolved_tools: dict[str, str | None] = {}
    for tool_name, required in (
        ("ffmpeg", True),
        ("ffprobe", True),
        ("yt-dlp", input_is_url),
        ("curl", mode == "hybrid" and not render_only),
    ):
        resolved = shutil.which(tool_name)
        resolved_tools[tool_name] = resolved
        add_check(
            f"tool:{tool_name}",
            required,
            bool(resolved) or not required,
            "available" if resolved else ("missing" if required else "not required"),
        )

    if render_only:
        segments_path = args.render_only.expanduser().resolve()
        input_ok = False
        detail = "segments.json missing or misplaced"
        if segments_path.is_file() and segments_path.parent.name == "03_索引记录":
            try:
                data = json.loads(segments_path.read_text(encoding="utf-8-sig"))
                normalize_and_validate_segments(data)
                package_root = segments_path.parent.parent
                source = resolve_package_member(
                    package_root,
                    data.get("source", {}).get("relative_path", "源视频.mp4"),
                )
                source_probe = probe_media(
                    source,
                    resolved_tools["ffprobe"],
                    timeout_seconds=min(args.media_timeout_seconds, 120),
                )
                input_ok = True
                detail = f"render package valid, duration_ms={source_probe['duration_ms']}"
            except Exception:
                detail = "segments, source path, timeline, or source media is invalid"
    elif input_is_url:
        input_ok = True
        detail = "supported TikTok HTTPS URL"
    else:
        local_path = Path(args.input).expanduser().resolve()
        input_ok = False
        detail = "local MP4 missing or invalid"
        if (
            local_path.is_file()
            and local_path.suffix.lower() == ".mp4"
            and resolved_tools["ffprobe"]
        ):
            try:
                source_probe = probe_media(
                    local_path,
                    resolved_tools["ffprobe"],
                    timeout_seconds=min(args.media_timeout_seconds, 120),
                )
                input_ok = True
                detail = f"valid local MP4, duration_ms={source_probe['duration_ms']}"
            except Exception:
                detail = "local file exists but media probing failed"
    add_check("input", True, input_ok, detail)

    if not render_only:
        output_parent = args.output_parent.expanduser().resolve()
        existing_parent = nearest_existing_parent(output_parent)
        output_ok = bool(
            existing_parent
            and existing_parent.is_dir()
            and os.access(existing_parent, os.W_OK)
        )
        add_check(
            "output_parent",
            True,
            output_ok,
            "writable parent available" if output_ok else "no writable parent",
        )

    credential_required = mode == "hybrid" and not render_only
    credential_ok = True
    credential_detail = "not required"
    if credential_required:
        try:
            _, credential_source = load_api_key(args.api_key_file)
            credential_detail = f"configured via {credential_source}"
        except (FileNotFoundError, ValueError):
            credential_ok = False
            credential_detail = "Ark API key missing or invalid"
    add_check("ark_credentials", credential_required, credential_ok, credential_detail)

    prompt_required = credential_required
    prompt_ok = True
    prompt_detail = "not required"
    if prompt_required:
        prompt_path = args.prompt_file.expanduser().resolve()
        prompt_ok = prompt_path.is_file() and bool(
            prompt_path.read_text(encoding="utf-8-sig").strip()
        )
        prompt_detail = "prompt available" if prompt_ok else "prompt missing or empty"
    add_check("semantic_prompt", prompt_required, prompt_ok, prompt_detail)

    if args.cookies_file:
        cookie_ok = args.cookies_file.expanduser().resolve().is_file()
        add_check(
            "cookie_file",
            True,
            cookie_ok,
            "cookie file available" if cookie_ok else "cookie file missing",
        )

    valid = all(check["ok"] for check in checks if check["required"])
    return {
        "schema_version": 1,
        "status": "valid" if valid else "invalid",
        "mode": "render-only" if render_only else mode,
        "network_required": input_is_url or credential_required,
        "api_called": False,
        "checks": checks,
    }
