#!/usr/bin/env python3
"""Download one explicitly provided TikTok URL and capture provenance metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from runtime_safety import (
    redact_command_paths,
    sanitize_source_url,
    subprocess_environment,
)


SUMMARY_FILENAME = "download-summary.json"
DEFAULT_DOWNLOAD_TIMEOUT_SECONDS = 1800


def validate_tiktok_url(value: str) -> str:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not hostname:
        raise ValueError("TikTok input must be an HTTPS URL")
    if hostname != "tiktok.com" and not hostname.endswith(".tiktok.com"):
        raise ValueError("Only tiktok.com URLs are supported")
    return value


def run_yt_dlp(
    arguments: list[str],
    action: str,
    *,
    timeout_seconds: int = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout_seconds,
        env=subprocess_environment(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-2000:] or completed.stdout.strip()[-2000:]
        raise RuntimeError(
            f"yt-dlp failed while {action}; no automatic retry: "
            f"{redact_command_paths(detail, arguments)}"
        )
    return completed


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_metadata(
    yt_dlp: str,
    url: str,
    cookie_arguments: list[str],
    timeout_seconds: int,
) -> dict:
    completed = run_yt_dlp(
        [
            yt_dlp,
            "--ignore-config",
            "--no-playlist",
            "--no-warnings",
            "--retries",
            "0",
            "--fragment-retries",
            "0",
            "--extractor-retries",
            "0",
            "--socket-timeout",
            "30",
            "--dump-single-json",
            *cookie_arguments,
            url,
        ],
        "reading TikTok metadata",
        timeout_seconds=min(timeout_seconds, 180),
    )
    try:
        metadata = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp returned invalid metadata JSON") from exc
    if not metadata.get("id"):
        raise RuntimeError("TikTok metadata does not contain a video ID")
    return metadata


def download_from_snapshot(
    yt_dlp: str,
    metadata: dict,
    output_dir: Path,
    overwrite: bool,
    cookie_arguments: list[str],
    timeout_seconds: int,
) -> Path:
    video_id = str(metadata["id"])
    expected_path = output_dir / f"{video_id}.mp4"
    summary_path = output_dir / SUMMARY_FILENAME
    conflicts = [path for path in (expected_path, summary_path) if path.exists()]
    if conflicts and not overwrite:
        names = ", ".join(path.name for path in conflicts)
        raise FileExistsError(
            f"Download outputs already exist: {names}; use another directory"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tiktok-download-") as temp_dir:
        snapshot_path = Path(temp_dir) / "metadata.json"
        snapshot_path.write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )
        command = [
            yt_dlp,
            "--ignore-config",
            "--no-playlist",
            "--no-warnings",
            "--retries",
            "0",
            "--fragment-retries",
            "0",
            "--extractor-retries",
            "0",
            "--socket-timeout",
            "30",
            "--load-info-json",
            str(snapshot_path),
            "--format",
            "best[ext=mp4]/best",
            "--merge-output-format",
            "mp4",
            "--output",
            str(output_dir / f"{video_id}.%(ext)s"),
            "--print",
            "after_move:filepath",
            *cookie_arguments,
            "--no-overwrites",
        ]
        completed = run_yt_dlp(
            command,
            "downloading TikTok media",
            timeout_seconds=timeout_seconds,
        )

    candidates = [
        Path(line.strip())
        for line in completed.stdout.splitlines()
        if line.strip() and Path(line.strip()).is_file()
    ]
    video_path = candidates[-1] if candidates else expected_path
    if not video_path.is_file():
        raise RuntimeError("yt-dlp completed but the downloaded file was not found")
    if video_path.suffix.lower() != ".mp4":
        raise RuntimeError(f"Downloaded media is not MP4: {video_path.name}")
    if video_path.stat().st_size <= 0:
        raise RuntimeError("Downloaded MP4 is empty")
    return video_path.resolve()


def download_tiktok(
    url: str,
    output_dir: Path,
    *,
    cookies_from_browser: str | None = None,
    cookies_file: Path | None = None,
    timeout_seconds: int = DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
) -> tuple[Path, dict]:
    validate_tiktok_url(url)
    if timeout_seconds <= 0:
        raise ValueError("Download timeout must be positive")
    yt_dlp = shutil.which("yt-dlp")
    if yt_dlp is None:
        raise RuntimeError("yt-dlp is required but was not found")

    if cookies_from_browser and cookies_file:
        raise ValueError("Choose either browser cookies or a cookie file, not both")
    cookie_arguments: list[str] = []
    authentication_mode = "none"
    if cookies_from_browser:
        cookie_arguments = ["--cookies-from-browser", cookies_from_browser]
        authentication_mode = "browser"
    elif cookies_file:
        resolved_cookie_file = cookies_file.expanduser().resolve()
        if not resolved_cookie_file.is_file():
            raise FileNotFoundError(f"Cookie file not found: {resolved_cookie_file}")
        cookie_arguments = ["--cookies", str(resolved_cookie_file)]
        authentication_mode = "file"

    metadata = load_metadata(yt_dlp, url, cookie_arguments, timeout_seconds)
    video_path = download_from_snapshot(
        yt_dlp, metadata, output_dir, False, cookie_arguments, timeout_seconds
    )
    version = run_yt_dlp(
        [yt_dlp, "--ignore-config", "--version"],
        "reading yt-dlp version",
        timeout_seconds=min(timeout_seconds, 30),
    ).stdout.strip()
    summary = {
        "schema_version": 1,
        "source_type": "tiktok_url",
        "source_url": sanitize_source_url(url),
        "resolved_url": sanitize_source_url(metadata.get("webpage_url", "")),
        "downloaded_at_utc": datetime.now(timezone.utc).isoformat(),
        "video": {
            "id": str(metadata["id"]),
            "uploader": metadata.get("uploader", ""),
            "title": metadata.get("title", ""),
            "duration_seconds": metadata.get("duration"),
            "path": video_path.name,
            "bytes": video_path.stat().st_size,
            "sha256": sha256_file(video_path),
        },
        "download": {
            "tool": "yt-dlp",
            "version": version,
            "method": "metadata_snapshot_then_load_info_json",
            "automatic_retry": False,
            "authentication": authentication_mode,
            "user_config_ignored": True,
        },
    }
    summary_path = output_dir / SUMMARY_FILENAME
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return video_path, summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", type=validate_tiktok_url)
    parser.add_argument("--output-dir", type=Path, required=True)
    authentication = parser.add_mutually_exclusive_group()
    authentication.add_argument("--cookies-from-browser")
    authentication.add_argument("--cookies-file", type=Path)
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=DEFAULT_DOWNLOAD_TIMEOUT_SECONDS,
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    _, summary = download_tiktok(
        args.url,
        args.output_dir.expanduser().resolve(),
        cookies_from_browser=args.cookies_from_browser,
        cookies_file=args.cookies_file,
        timeout_seconds=args.timeout_seconds,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileNotFoundError,
        FileExistsError,
        RuntimeError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as exc:
        raise SystemExit(f"Error: {exc}") from exc
