#!/usr/bin/env python3
"""Deterministic naming, output-contract, and pipeline-state helpers."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import statistics
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

from runtime_safety import redact_sensitive_text


SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Imported by the CLI for deterministic output contracts and pipeline state."
SKILL_VERSION = "0.1.0-beta.2"
INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")


class AssetizeError(RuntimeError):
    """Expected workflow failure suitable for a concise CLI error."""


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def write_json(path: Path, payload: dict) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_component(value: str, *, max_chars: int, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = INVALID_FILENAME_RE.sub(" ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ._-—")
    normalized = normalized.replace(" ", "_")
    normalized = re.sub(r"_+", "_", normalized)
    normalized = normalized[:max_chars].rstrip(" ._-—")
    return normalized or fallback


def package_name(video_title: str, video_id: str, run_date: str) -> str:
    title = sanitize_component(video_title, max_chars=40, fallback="TikTok视频")
    safe_id = sanitize_component(video_id, max_chars=40, fallback="unknown")
    return f"视频拆解-{title}-{safe_id}-{run_date}"


def segment_stem(index: int, purpose: str, content: str) -> str:
    safe_purpose = sanitize_component(purpose, max_chars=12, fallback="其他")
    safe_content = sanitize_component(content, max_chars=18, fallback=f"镜头{index:03d}")
    return f"{index:03d}_{safe_purpose}_{safe_content}"


def compact_timestamp(timestamp_ms: int) -> str:
    total_seconds = max(0, int(timestamp_ms)) // 1000
    minutes, seconds = divmod(total_seconds, 60)
    return f"{minutes:02d}m{seconds:02d}s"


def cover_timestamp(candidate: dict) -> int:
    duration = candidate["duration_ms"]
    if duration <= 300:
        return candidate["start_ms"] + duration // 2
    inset = max(150, round(duration * 0.35))
    inset = min(inset, duration - 150)
    return candidate["start_ms"] + inset


def build_final_segments(groups: list[dict], candidates: list[dict]) -> list[dict]:
    candidate_lookup = {candidate["id"]: candidate for candidate in candidates}
    segments = []
    for index, group in enumerate(groups, start=1):
        first = candidate_lookup[group["candidate_ids"][0]]
        last = candidate_lookup[group["candidate_ids"][-1]]
        cover_candidate = candidate_lookup[group["cover_candidate_id"]]
        stem = segment_stem(index, group["purpose"], group["content"])
        segments.append(
            {
                "index": index,
                "start_ms": first["start_ms"],
                "end_ms": last["end_ms"],
                "duration_ms": last["end_ms"] - first["start_ms"],
                "purpose": group["purpose"],
                "content": group["content"],
                "candidate_ids": group["candidate_ids"],
                "cover_candidate_id": group["cover_candidate_id"],
                "cover_ms": cover_timestamp(cover_candidate),
                "confidence": group["confidence"],
                "confidence_type": group.get("confidence_type", "unknown"),
                "file_stem": stem,
                "clip": f"01_分镜视频/{stem}.mp4",
                "keyframe": f"02_关键帧/{stem}.jpg",
            }
        )
    return segments


def normalize_and_validate_segments(data: dict) -> list[dict]:
    source = data.get("source", {})
    try:
        source_duration = int(source["duration_ms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AssetizeError("segments.json source.duration_ms is invalid") from exc
    raw_segments = data.get("segments")
    if not isinstance(raw_segments, list) or not raw_segments:
        raise AssetizeError("segments.json must contain a non-empty segments list")

    normalized = []
    expected_start = 0
    for expected_index, raw in enumerate(raw_segments, start=1):
        if not isinstance(raw, dict):
            raise AssetizeError(f"Segment {expected_index} must be an object")
        try:
            start_ms = int(raw["start_ms"])
            end_ms = int(raw["end_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AssetizeError(f"Segment {expected_index} has invalid timestamps") from exc
        if start_ms != expected_start:
            raise AssetizeError(
                f"Segment {expected_index} starts at {start_ms}ms; expected {expected_start}ms"
            )
        if end_ms <= start_ms:
            raise AssetizeError(f"Segment {expected_index} has non-positive duration")
        purpose = str(raw.get("purpose") or "其他").strip()
        content = str(raw.get("content") or f"镜头{expected_index:03d}").strip()
        stem = segment_stem(expected_index, purpose, content)
        cover_ms = int(raw.get("cover_ms", start_ms + (end_ms - start_ms) // 2))
        if not start_ms <= cover_ms < end_ms:
            cover_ms = start_ms + (end_ms - start_ms) // 2
        normalized_segment = dict(raw)
        normalized_segment.update(
            {
                "index": expected_index,
                "start_ms": start_ms,
                "end_ms": end_ms,
                "duration_ms": end_ms - start_ms,
                "purpose": purpose,
                "content": sanitize_component(
                    content, max_chars=18, fallback=f"镜头{expected_index:03d}"
                ),
                "cover_ms": cover_ms,
                "file_stem": stem,
                "clip": f"01_分镜视频/{stem}.mp4",
                "keyframe": f"02_关键帧/{stem}.jpg",
            }
        )
        normalized.append(normalized_segment)
        expected_start = end_ms
    if expected_start != source_duration:
        raise AssetizeError(
            f"Segments end at {expected_start}ms; source duration is {source_duration}ms"
        )
    return normalized


def write_manifest_csv(path: Path, segments: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as destination:
        writer = csv.DictWriter(
            destination,
            fieldnames=[
                "序号",
                "混剪用途",
                "画面内容",
                "开始时间毫秒",
                "结束时间毫秒",
                "时长秒",
                "分镜视频",
                "关键帧",
                "置信度",
            ],
        )
        writer.writeheader()
        for segment in segments:
            writer.writerow(
                {
                    "序号": segment["index"],
                    "混剪用途": segment["purpose"],
                    "画面内容": segment["content"],
                    "开始时间毫秒": segment["start_ms"],
                    "结束时间毫秒": segment["end_ms"],
                    "时长秒": f"{segment['duration_ms'] / 1000:.3f}",
                    "分镜视频": segment["clip"],
                    "关键帧": segment["keyframe"],
                    "置信度": segment.get("confidence", ""),
                }
            )
    os.replace(temporary, path)


def local_input_summary(path: Path) -> tuple[str, str, dict]:
    digest = sha256_file(path)
    video_id = f"local-{digest[:12]}"
    summary = {
        "schema_version": 1,
        "source_type": "local_mp4",
        "source_url": None,
        "downloaded_at_utc": None,
        "video": {
            "id": video_id,
            "uploader": "",
            "title": path.stem,
            "duration_seconds": None,
            "path": "源视频.mp4",
            "bytes": path.stat().st_size,
            "sha256": digest,
        },
        "download": None,
    }
    return video_id, path.stem, summary


def reused_download_summary(path: Path, source: Path) -> tuple[str, str, dict]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"Download summary not found: {resolved}")
    try:
        summary = json.loads(resolved.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Download summary is not valid JSON: {resolved}") from exc
    if summary.get("source_type") != "tiktok_url":
        raise ValueError("Reused download summary must come from a TikTok URL run")
    video = summary.get("video")
    if not isinstance(video, dict) or not video.get("id"):
        raise ValueError("Reused download summary does not contain a video ID")
    expected_sha256 = str(video.get("sha256") or "")
    actual_sha256 = sha256_file(source)
    if not expected_sha256 or expected_sha256 != actual_sha256:
        raise ValueError(
            "Local MP4 does not match the SHA-256 in the reused download summary"
        )
    reused = json.loads(json.dumps(summary))
    download = reused.get("download")
    if isinstance(download, dict):
        download["reused_for_assetization"] = True
    reused["reuse"] = {
        "mode": "verified_local_source_with_tiktok_metadata",
        "sha256_verified": True,
    }
    title = str(video.get("title") or "TikTok视频")
    return str(video["id"]), title, reused


def update_download_summary_for_package(summary: dict, source: Path) -> dict:
    updated = json.loads(json.dumps(summary))
    updated["video"]["path"] = "源视频.mp4"
    updated["video"]["bytes"] = source.stat().st_size
    updated["video"]["sha256"] = sha256_file(source)
    return updated


def resource_preflight(
    source: Path,
    output_parent: Path,
    *,
    render_clips: bool,
    hybrid_mode: bool,
    max_hybrid_upload_mb: int,
) -> dict:
    source_bytes = source.stat().st_size
    upload_limit_bytes = max_hybrid_upload_mb * 1024 * 1024
    if hybrid_mode and source_bytes > upload_limit_bytes:
        raise AssetizeError(
            "Hybrid upload blocked by the configured size limit: "
            f"{source_bytes} bytes exceeds {upload_limit_bytes} bytes"
        )
    estimated_disk_bytes = round(
        source_bytes * (3.0 if render_clips else 1.5) + 64 * 1024 * 1024
    )
    free_disk_bytes = shutil.disk_usage(output_parent).free
    if free_disk_bytes < estimated_disk_bytes:
        raise AssetizeError(
            "Insufficient free disk space for a safe run: "
            f"need approximately {estimated_disk_bytes} bytes, "
            f"available {free_disk_bytes} bytes"
        )
    return {
        "source_bytes": source_bytes,
        "estimated_disk_bytes": estimated_disk_bytes,
        "free_disk_bytes_before_run": free_disk_bytes,
        "hybrid_upload_limit_bytes": upload_limit_bytes if hybrid_mode else None,
    }


def build_quality_summary(
    candidates: list[dict], duration_ms: int, detection: dict
) -> dict:
    durations = [candidate["duration_ms"] for candidate in candidates]
    shots_per_minute = len(candidates) / max(duration_ms / 60000, 1 / 60)
    reasons: list[str] = []
    status = "normal"
    if detection.get("events_truncated"):
        status = "degraded"
        reasons.append("scene_events_truncated")
    if len(candidates) == 1 and duration_ms >= 30000:
        status = "review_recommended" if status == "normal" else status
        reasons.append("long_video_with_single_candidate")
    if shots_per_minute > 60:
        status = "review_recommended" if status == "normal" else status
        reasons.append("unusually_high_shot_density")
    if max(durations) >= 60000:
        status = "review_recommended" if status == "normal" else status
        reasons.append("candidate_longer_than_60_seconds")
    return {
        "status": status,
        "reasons": reasons,
        "shots_per_minute": round(shots_per_minute, 3),
        "duration_ms": {
            "minimum": min(durations),
            "median": round(statistics.median(durations)),
            "maximum": max(durations),
        },
        "human_review_recommended": status != "normal",
    }


def write_stage_summary(
    index_dir: Path,
    *,
    status: str,
    stage: str,
    error: BaseException | None = None,
    recoverable: bool = False,
    next_action: str | None = None,
) -> None:
    payload = {
        "schema_version": 2,
        "skill_version": SKILL_VERSION,
        "status": status,
        "stage": stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "recoverable": recoverable,
        "next_action": next_action,
    }
    if error is not None:
        payload["error"] = {
            "type": type(error).__name__,
            "message": redact_sensitive_text(str(error)),
        }
    write_json(index_dir / "run-summary.json", payload)


def nearest_existing_parent(path: Path) -> Path | None:
    candidate = path.expanduser().resolve()
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() else None
