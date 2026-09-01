#!/usr/bin/env python3
"""Turn one TikTok URL or local MP4 into a CapCut-ready shot asset package."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from download_tiktok import download_tiktok, validate_tiktok_url
from package_contracts import (
    SKILL_VERSION,
    AssetizeError,
    build_final_segments,
    build_quality_summary,
    compact_timestamp,
    local_input_summary,
    normalize_and_validate_segments,
    package_name,
    resource_preflight,
    reused_download_summary,
    sanitize_component,
    segment_stem,
    sha256_file,
    update_download_summary_for_package,
    write_json,
    write_manifest_csv,
    write_stage_summary,
)
from preflight_checks import build_preflight_report as build_preflight_report_impl
from runtime_safety import (
    redact_command_paths,
    redact_sensitive_text,
    resolve_package_member,
    sanitize_source_url,
    subprocess_environment,
)


API_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
DEFAULT_MODEL = "doubao-seed-2-0-lite-260428"
DEFAULT_MEDIA_TIMEOUT_SECONDS = 1800
DEFAULT_MAX_HYBRID_UPLOAD_MB = 200
DEFAULT_MAX_SCENE_EVENTS = 120
DEFAULT_SCENE_THRESHOLD: str | float = "auto"
DEFAULT_MANUAL_SCENE_THRESHOLD = 0.20
DEFAULT_MIN_SHOT_SECONDS = 0.60
AUTO_SCAN_THRESHOLD = 0.10
AUTO_SCENE_PROFILES = (
    {
        "material_profile": "稳定产品展示",
        "scene_threshold": 0.22,
        "min_shot_seconds": 0.80,
    },
    {
        "material_profile": "口播/教程演示",
        "scene_threshold": 0.30,
        "min_shot_seconds": 0.80,
    },
    {
        "material_profile": "快节奏演示",
        "scene_threshold": 0.35,
        "min_shot_seconds": 0.80,
    },
    {
        "material_profile": "快节奏混剪",
        "scene_threshold": 0.45,
        "min_shot_seconds": 0.60,
    },
)
DEFAULT_PROMPT_FILE = (
    Path(__file__).resolve().parent.parent
    / "references"
    / "shot-label-prompt.md"
)
PURPOSES = {
    "开场钩子",
    "痛点展示",
    "情绪反应",
    "商品露出",
    "功能介绍",
    "使用演示",
    "前后对比",
    "效果证明",
    "价格促销",
    "CTA",
    "收尾展示",
    "过渡空镜",
    "其他",
}
SCENE_TIME_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")
SCENE_SCORE_RE = re.compile(r"lavfi\.scene_score=([0-9]+(?:\.[0-9]+)?)")
API_KEY_PATTERNS = (
    re.compile(
        r"(?im)^\s*(?:ARK_API_KEY|api\s*key)\s*[：:=]\s*['\"]?"
        r"(ark-[A-Za-z0-9._-]{20,})"
    ),
    re.compile(
        r"(?<![A-Za-z0-9._-])(ark-[A-Za-z0-9._-]{20,})"
        r"(?![A-Za-z0-9._-])"
    ),
)


def parse_scene_threshold(value: str | float) -> str | float:
    text = str(value).strip().lower()
    if text == "auto":
        return "auto"
    try:
        return float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "--scene-threshold must be a number between 0.01 and 1, or auto"
        ) from exc


def resolved_min_shot_seconds(value: float | None) -> float:
    return value if value is not None else DEFAULT_MIN_SHOT_SECONDS


def run_command(
    arguments: list[str],
    action: str,
    *,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        arguments,
        text=True,
        capture_output=True,
        check=False,
        timeout=timeout,
        env=subprocess_environment(),
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip()[-3000:] or completed.stdout.strip()[-3000:]
        raise AssetizeError(f"{action} failed: {redact_command_paths(detail, arguments)}")
    return completed


def ffmpeg_tools() -> tuple[str, str]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise AssetizeError("FFmpeg and FFprobe are required")
    return ffmpeg, ffprobe


def tool_version(executable: str) -> str:
    completed = run_command(
        [executable, "-version"],
        f"reading {Path(executable).name} version",
        timeout=30,
    )
    return completed.stdout.splitlines()[0].strip()


def parse_rate(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        numerator, denominator = value.split("/", 1)
        if float(denominator) == 0:
            return None
        return float(numerator) / float(denominator)
    except (ValueError, ZeroDivisionError):
        return None


def probe_media(
    path: Path,
    ffprobe: str,
    *,
    timeout_seconds: int = DEFAULT_MEDIA_TIMEOUT_SECONDS,
) -> dict:
    completed = run_command(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name,size:stream=index,codec_type,codec_name,width,height,pix_fmt,r_frame_rate,avg_frame_rate,sample_rate,channels",
            "-of",
            "json",
            str(path),
        ],
        f"probing {path.name}",
        timeout=timeout_seconds,
    )
    try:
        raw = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssetizeError(f"FFprobe returned invalid JSON for {path.name}") from exc

    streams = raw.get("streams", [])
    video_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "video"), None
    )
    audio_stream = next(
        (stream for stream in streams if stream.get("codec_type") == "audio"), None
    )
    if not video_stream:
        raise AssetizeError(f"No video stream found in {path.name}")
    try:
        duration_seconds = float(raw.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        duration_seconds = 0
    if duration_seconds <= 0:
        raise AssetizeError(f"Invalid duration for {path.name}")

    format_names = str(raw.get("format", {}).get("format_name", "")).split(",")
    video_codec = str(video_stream.get("codec_name", ""))
    audio_codec = str(audio_stream.get("codec_name", "")) if audio_stream else None
    pixel_format = str(video_stream.get("pix_fmt", ""))
    capcut_compatible = (
        bool({"mov", "mp4"}.intersection(format_names))
        and video_codec == "h264"
        and pixel_format.startswith("yuv420p")
        and (audio_stream is None or audio_codec == "aac")
    )
    frame_rate = parse_rate(video_stream.get("avg_frame_rate")) or parse_rate(
        video_stream.get("r_frame_rate")
    )
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "duration_ms": round(duration_seconds * 1000),
        "format_names": format_names,
        "video": {
            "codec": video_codec,
            "width": int(video_stream.get("width") or 0),
            "height": int(video_stream.get("height") or 0),
            "pixel_format": pixel_format,
            "frame_rate": round(frame_rate, 6) if frame_rate else None,
        },
        "audio": (
            {
                "codec": audio_codec,
                "sample_rate": int(audio_stream.get("sample_rate") or 0),
                "channels": int(audio_stream.get("channels") or 0),
            }
            if audio_stream
            else None
        ),
        "capcut_compatible_encoding_profile": capcut_compatible,
    }


def score_distribution(events: list[dict]) -> dict:
    scores = sorted(float(event["score"]) for event in events)
    if not scores:
        return {
            "count": 0,
            "mean": 0.0,
            "median": 0.0,
            "p90": 0.0,
            "maximum": 0.0,
        }
    p90_index = min(len(scores) - 1, math.ceil(len(scores) * 0.90) - 1)
    return {
        "count": len(scores),
        "mean": round(sum(scores) / len(scores), 6),
        "median": round(statistics.median(scores), 6),
        "p90": round(scores[p90_index], 6),
        "maximum": round(scores[-1], 6),
    }


def candidate_duration_summary(candidates: list[dict]) -> dict:
    durations = [candidate["duration_ms"] for candidate in candidates]
    return {
        "minimum": min(durations),
        "median": round(statistics.median(durations)),
        "maximum": max(durations),
    }


def shots_per_minute(candidate_count: int, duration_ms: int) -> float:
    return candidate_count / max(duration_ms / 60000, 1 / 60)


def motion_intensity_label(score_summary: dict) -> str:
    if score_summary["count"] == 0:
        return "minimal"
    if score_summary["p90"] >= 0.65:
        return "high"
    if score_summary["p90"] >= 0.35:
        return "medium"
    return "low"


def collect_scene_events(
    video: Path,
    ffmpeg: str,
    *,
    scan_threshold: float = AUTO_SCAN_THRESHOLD,
    timeout_seconds: int = DEFAULT_MEDIA_TIMEOUT_SECONDS,
) -> list[dict]:
    completed = run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-nostats",
            "-i",
            str(video),
            "-vf",
            f"select='gt(scene,{scan_threshold:.6f})',metadata=print:file=-",
            "-an",
            "-f",
            "null",
            "-",
        ],
        "detecting scene changes",
        timeout=timeout_seconds,
    )

    events: list[dict] = []
    pending_time: float | None = None
    for line in completed.stdout.splitlines():
        time_match = SCENE_TIME_RE.search(line)
        if time_match:
            pending_time = float(time_match.group(1))
            continue
        score_match = SCENE_SCORE_RE.search(line)
        if score_match and pending_time is not None:
            score = float(score_match.group(1))
            if score >= scan_threshold:
                events.append(
                    {"time_ms": round(pending_time * 1000), "score": round(score, 6)}
                )
            pending_time = None
    return events


def summarize_scene_events(
    scanned_events: list[dict],
    *,
    scene_threshold: float,
    scan_threshold: float,
    min_event_gap_ms: int = 250,
    max_events: int = DEFAULT_MAX_SCENE_EVENTS,
) -> tuple[list[dict], dict]:
    events = [
        event for event in scanned_events if float(event["score"]) >= scene_threshold
    ]
    clustered: list[dict] = []
    for event in sorted(events, key=lambda item: item["time_ms"]):
        if clustered and event["time_ms"] - clustered[-1]["time_ms"] < min_event_gap_ms:
            if event["score"] > clustered[-1]["score"]:
                clustered[-1] = event
        else:
            clustered.append(event)

    clustered_count = len(clustered)
    kept = clustered
    if clustered_count > max_events:
        kept = sorted(
            sorted(clustered, key=lambda item: item["score"], reverse=True)[:max_events],
            key=lambda item: item["time_ms"],
        )
    statistics_payload = {
        "raw_event_count": len(events),
        "clustered_event_count": clustered_count,
        "kept_event_count": len(kept),
        "events_truncated": clustered_count > max_events,
        "max_events": max_events,
        "scan_threshold": round(scan_threshold, 6),
        "score_summary": score_distribution(events),
        "scan_score_summary": score_distribution(scanned_events),
    }
    return kept, statistics_payload


def detect_scene_events(
    video: Path,
    ffmpeg: str,
    *,
    scene_threshold: float,
    min_event_gap_ms: int = 250,
    max_events: int = DEFAULT_MAX_SCENE_EVENTS,
    timeout_seconds: int = DEFAULT_MEDIA_TIMEOUT_SECONDS,
) -> tuple[list[dict], dict]:
    scan_threshold = min(scene_threshold, AUTO_SCAN_THRESHOLD)
    scanned_events = collect_scene_events(
        video,
        ffmpeg,
        scan_threshold=scan_threshold,
        timeout_seconds=timeout_seconds,
    )
    return summarize_scene_events(
        scanned_events,
        scene_threshold=scene_threshold,
        scan_threshold=scan_threshold,
        min_event_gap_ms=min_event_gap_ms,
        max_events=max_events,
    )


def build_candidate_shots(
    events: list[dict],
    duration_ms: int,
    *,
    min_shot_ms: int,
) -> list[dict]:
    boundaries = [{"time_ms": 0, "score": None}]
    boundaries.extend(
        event
        for event in events
        if min_shot_ms // 2 < event["time_ms"] < duration_ms - min_shot_ms // 2
    )
    boundaries.append({"time_ms": duration_ms, "score": None})
    boundaries.sort(key=lambda item: item["time_ms"])

    deduplicated: list[dict] = []
    for boundary in boundaries:
        if deduplicated and boundary["time_ms"] == deduplicated[-1]["time_ms"]:
            previous_score = deduplicated[-1]["score"] or 0
            current_score = boundary["score"] or 0
            if current_score > previous_score:
                deduplicated[-1] = boundary
        else:
            deduplicated.append(boundary)
    boundaries = deduplicated

    while len(boundaries) > 2:
        short_index = next(
            (
                index
                for index in range(len(boundaries) - 1)
                if boundaries[index + 1]["time_ms"] - boundaries[index]["time_ms"]
                < min_shot_ms
            ),
            None,
        )
        if short_index is None:
            break
        if short_index == 0:
            del boundaries[1]
            continue
        if short_index == len(boundaries) - 2:
            del boundaries[-2]
            continue
        left_score = float(boundaries[short_index].get("score") or 0)
        right_score = float(boundaries[short_index + 1].get("score") or 0)
        if left_score <= right_score:
            del boundaries[short_index]
        else:
            del boundaries[short_index + 1]

    candidates = []
    for index, (start, end) in enumerate(zip(boundaries, boundaries[1:]), start=1):
        candidates.append(
            {
                "id": index,
                "start_ms": int(start["time_ms"]),
                "end_ms": int(end["time_ms"]),
                "duration_ms": int(end["time_ms"] - start["time_ms"]),
                "start_scene_score": start.get("score"),
                "end_scene_score": end.get("score"),
            }
        )
    if not candidates:
        raise AssetizeError("Scene detection did not produce any candidate shots")
    return candidates


def build_auto_tuning_plan(
    scanned_events: list[dict],
    duration_ms: int,
    *,
    max_events: int,
    min_shot_seconds_override: float | None = None,
) -> dict:
    scan_summary = score_distribution(scanned_events)
    trials = []
    for profile in AUTO_SCENE_PROFILES:
        min_shot_seconds = (
            min_shot_seconds_override
            if min_shot_seconds_override is not None
            else float(profile["min_shot_seconds"])
        )
        scene_threshold = float(profile["scene_threshold"])
        events, detection_statistics = summarize_scene_events(
            scanned_events,
            scene_threshold=scene_threshold,
            scan_threshold=AUTO_SCAN_THRESHOLD,
            max_events=max_events,
        )
        candidates = build_candidate_shots(
            events,
            duration_ms,
            min_shot_ms=round(min_shot_seconds * 1000),
        )
        trials.append(
            {
                "material_profile": profile["material_profile"],
                "scene_threshold": scene_threshold,
                "min_shot_seconds": min_shot_seconds,
                "min_shot_ms": round(min_shot_seconds * 1000),
                "events": events,
                "statistics": detection_statistics,
                "candidates": candidates,
                "candidate_count": len(candidates),
                "shots_per_minute": round(
                    shots_per_minute(len(candidates), duration_ms), 3
                ),
                "duration_ms": candidate_duration_summary(candidates),
            }
        )

    by_threshold = {
        round(trial["scene_threshold"], 2): trial for trial in trials
    }
    density_030 = by_threshold[0.30]["shots_per_minute"]
    density_035 = by_threshold[0.35]["shots_per_minute"]
    density_045 = by_threshold[0.45]["shots_per_minute"]
    reasons: list[str] = []

    if density_045 >= 28:
        selected = by_threshold[0.45]
        material_profile = "快节奏混剪"
        reasons.append("0.45 阈值下仍保持较高分镜密度，判断为快节奏混剪。")
    elif density_030 > 36 and density_035 < density_030:
        selected = by_threshold[0.35]
        material_profile = "快节奏演示"
        reasons.append("0.30 阈值下分镜密度偏高，提高到 0.35 以减少碎切。")
    elif density_030 >= 10:
        selected = by_threshold[0.30]
        material_profile = "口播/教程演示"
        reasons.append("0.30 阈值下分镜密度处于教程/口播素材的可用区间。")
    else:
        selected = by_threshold[0.22]
        material_profile = "稳定产品展示"
        reasons.append("画面变化密度偏低，降低阈值以保留产品角度或步骤变化。")

    if min_shot_seconds_override is None:
        reasons.append(
            f"自动选择最短分镜 {selected['min_shot_seconds']:.2f}s。"
        )
    else:
        reasons.append(
            f"沿用用户指定的最短分镜 {selected['min_shot_seconds']:.2f}s。"
        )

    public_trials = [
        {
            "material_profile": trial["material_profile"],
            "scene_threshold": trial["scene_threshold"],
            "min_shot_seconds": round(trial["min_shot_seconds"], 3),
            "candidate_count": trial["candidate_count"],
            "shots_per_minute": trial["shots_per_minute"],
            "duration_ms": trial["duration_ms"],
            "raw_event_count": trial["statistics"]["raw_event_count"],
            "clustered_event_count": trial["statistics"]["clustered_event_count"],
            "kept_event_count": trial["statistics"]["kept_event_count"],
            "events_truncated": trial["statistics"]["events_truncated"],
        }
        for trial in trials
    ]

    selected["statistics"]["auto_tuning"] = {
        "requested": True,
        "strategy": "local_scene_density_v1",
        "material_profile": material_profile,
        "motion_intensity": motion_intensity_label(scan_summary),
        "selected_scene_threshold": selected["scene_threshold"],
        "selected_min_shot_seconds": round(selected["min_shot_seconds"], 3),
        "min_shot_seconds_source": (
            "user" if min_shot_seconds_override is not None else "auto"
        ),
        "scan": {
            "threshold": AUTO_SCAN_THRESHOLD,
            "event_count": len(scanned_events),
            "score_summary": scan_summary,
        },
        "trials": public_trials,
        "reasons": reasons,
    }
    return selected


def extract_api_key(content: str) -> str:
    for pattern in API_KEY_PATTERNS:
        match = pattern.search(content)
        if match:
            return match.group(1)
    raise ValueError("Credential source does not contain an Ark API key")


def load_api_key(argument_path: Path | None) -> tuple[str, str]:
    if argument_path is not None:
        resolved = argument_path.expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"API key file not found: {resolved}")
        return extract_api_key(resolved.read_text(encoding="utf-8-sig")), "argument-file"
    environment_key = os.environ.get("ARK_API_KEY", "").strip()
    if environment_key:
        return extract_api_key(environment_key), "environment"
    environment_file = os.environ.get("ARK_API_KEY_FILE", "").strip()
    if environment_file:
        resolved = Path(environment_file).expanduser().resolve()
        if not resolved.is_file():
            raise FileNotFoundError(f"API key file not found: {resolved}")
        return extract_api_key(resolved.read_text(encoding="utf-8-sig")), "environment-file"
    raise ValueError(
        "Ark API key is not configured; use --api-key-file, ARK_API_KEY, or ARK_API_KEY_FILE"
    )


def post_model_request(api_key: str, payload: dict, timeout_seconds: int) -> dict:
    curl = shutil.which("curl")
    if not curl:
        raise AssetizeError("curl is required for the model request")
    if "\n" in api_key or "\r" in api_key:
        raise ValueError("API key must not contain newlines")

    import tempfile

    curl_config = "\n".join(
        [
            f'url = "{API_URL}"',
            'request = "POST"',
            'header = "Content-Type: application/json"',
            f'header = "Authorization: Bearer {api_key}"',
            "silent",
            "show-error",
        ]
    ) + "\n"
    with tempfile.TemporaryDirectory(prefix="tiktok-shot-model-") as temp_dir:
        temp_path = Path(temp_dir)
        payload_path = temp_path / "payload.json"
        body_path = temp_path / "body.json"
        payload_path.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        completed = subprocess.run(
            [
                curl,
                "--config",
                "-",
                "--data-binary",
                f"@{payload_path}",
                "--output",
                str(body_path),
                "--write-out",
                "%{http_code}",
                "--connect-timeout",
                "30",
                "--max-time",
                str(timeout_seconds),
            ],
            input=curl_config,
            text=True,
            capture_output=True,
            timeout=timeout_seconds + 20,
            check=False,
            env=subprocess_environment(),
        )
        if completed.returncode != 0:
            raise AssetizeError(
                "Model request failed without automatic retry: "
                f"{redact_command_paths(completed.stderr, [str(payload_path), str(body_path)], max_chars=1000)}"
            )
        http_status = completed.stdout.strip()[-3:]
        raw_body = body_path.read_text(encoding="utf-8", errors="replace")

    if http_status.isdigit() and int(http_status) >= 400:
        try:
            error_payload = json.loads(raw_body)
            error = error_payload.get("error", {})
            code = error.get("code", "")
            message = error.get("message", "unknown API error")
        except json.JSONDecodeError:
            code = ""
            message = raw_body[:1000]
        raise AssetizeError(
            f"Doubao HTTP {http_status} {code}: "
            f"{redact_sensitive_text(str(message), max_chars=1000)}"
        )
    try:
        return json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise AssetizeError("Doubao returned a non-JSON response") from exc


def response_content(response: dict) -> str:
    choices = response.get("choices", [])
    if not choices:
        raise AssetizeError("Doubao response does not contain choices")
    content = choices[0].get("message", {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        raise AssetizeError("Doubao returned empty content")
    return content.strip()


def parse_json_object(content: str) -> dict:
    cleaned = content.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, count=1)
        cleaned = re.sub(r"\s*```$", "", cleaned, count=1)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise AssetizeError("Model output does not contain a JSON object")
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AssetizeError("Model output is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise AssetizeError("Model output must be a JSON object")
    return parsed


def normalized_usage(response: dict) -> dict:
    raw = response.get("usage")
    if not isinstance(raw, dict) or not raw:
        return {
            "available": False,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
        }
    input_tokens = int(raw.get("prompt_tokens", raw.get("input_tokens", 0)) or 0)
    output_tokens = int(raw.get("completion_tokens", raw.get("output_tokens", 0)) or 0)
    return {
        "available": True,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(raw.get("total_tokens", input_tokens + output_tokens) or 0),
    }


def validate_semantic_plan(plan: dict, candidate_count: int) -> list[dict]:
    groups = plan.get("segments")
    if not isinstance(groups, list) or not groups:
        raise AssetizeError("Model plan must contain a non-empty segments list")

    flattened: list[int] = []
    normalized = []
    for group_index, group in enumerate(groups, start=1):
        if not isinstance(group, dict):
            raise AssetizeError(f"Segment group {group_index} must be an object")
        ids = group.get("candidate_ids")
        if (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(value, int) for value in ids)
        ):
            raise AssetizeError(f"Segment group {group_index} has invalid candidate_ids")
        if len(ids) != 1:
            raise AssetizeError(
                f"Segment group {group_index} must label exactly one candidate; "
                "hybrid mode cannot merge physical shots"
            )
        purpose = str(group.get("purpose", "")).strip()
        if purpose not in PURPOSES:
            raise AssetizeError(f"Segment group {group_index} has unsupported purpose: {purpose}")
        content = str(group.get("content", "")).strip()
        if not content:
            raise AssetizeError(f"Segment group {group_index} has empty content")
        sanitized_content = sanitize_component(
            content, max_chars=18, fallback=f"镜头{group_index:03d}"
        )
        if len(sanitized_content) < 2:
            raise AssetizeError(
                f"Segment group {group_index} content is too short after sanitization"
            )
        cover_id = group.get("cover_candidate_id")
        if cover_id not in ids:
            raise AssetizeError(
                f"Segment group {group_index} cover_candidate_id is outside the group"
            )
        try:
            confidence = float(group.get("confidence"))
        except (TypeError, ValueError) as exc:
            raise AssetizeError(f"Segment group {group_index} has invalid confidence") from exc
        if not 0 <= confidence <= 1:
            raise AssetizeError(f"Segment group {group_index} confidence is outside 0..1")
        flattened.extend(ids)
        normalized.append(
            {
                "candidate_ids": ids,
                "purpose": purpose,
                "content": sanitized_content,
                "cover_candidate_id": cover_id,
                "confidence": round(confidence, 4),
                "confidence_type": "model_self_reported",
            }
        )

    expected = list(range(1, candidate_count + 1))
    if flattened != expected:
        raise AssetizeError(
            "Model plan must use every candidate exactly once in original order"
        )
    return normalized


def validate_grouped_plan(plan: dict, candidate_count: int) -> list[dict]:
    """Backward-compatible name for callers using the earlier public helper."""
    return validate_semantic_plan(plan, candidate_count)


def call_semantic_model(
    video: Path,
    candidates: list[dict],
    *,
    title: str,
    api_key_file: Path | None,
    prompt_file: Path,
    model: str,
    fps: float,
    max_tokens: int,
    timeout_seconds: int,
) -> tuple[list[dict], dict]:
    resolved_prompt = prompt_file.expanduser().resolve()
    if not resolved_prompt.is_file():
        raise FileNotFoundError(f"Prompt file not found: {resolved_prompt}")
    prompt = resolved_prompt.read_text(encoding="utf-8-sig").strip()
    if not prompt:
        raise ValueError("Model prompt must not be empty")

    candidate_input = {
        "video_title": title,
        "candidate_shots": [
            {
                "id": candidate["id"],
                "start_ms": candidate["start_ms"],
                "end_ms": candidate["end_ms"],
                "duration_ms": candidate["duration_ms"],
            }
            for candidate in candidates
        ],
    }
    full_prompt = (
        prompt
        + "\n\n以下是本次必须完整覆盖的候选镜头数据：\n"
        + json.dumps(candidate_input, ensure_ascii=False, indent=2)
    )
    api_key, credential_source = load_api_key(api_key_file)
    video_bytes = video.read_bytes()
    video_url = "data:video/mp4;base64," + base64.b64encode(video_bytes).decode("ascii")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "video_url", "video_url": {"url": video_url, "fps": fps}},
                    {"type": "text", "text": full_prompt},
                ],
            }
        ],
        "thinking": {"type": "disabled"},
        "max_tokens": max_tokens,
        "stream": False,
    }
    started = time.monotonic()
    response = post_model_request(api_key, payload, timeout_seconds)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    parsed = parse_json_object(response_content(response))
    groups = validate_semantic_plan(parsed, len(candidates))
    choices = response.get("choices", [{}])
    model_summary = {
        "mode": "hybrid",
        "provider": "volcengine-ark",
        "endpoint": API_URL,
        "model_requested": model,
        "model_returned": response.get("model", ""),
        "response_id": response.get("id", ""),
        "finish_reason": choices[0].get("finish_reason", ""),
        "elapsed_ms": elapsed_ms,
        "fps_requested": fps,
        "prompt": {
            "path": f"references/{resolved_prompt.name}",
            "sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        },
        "credential_source": credential_source,
        "usage": normalized_usage(response),
        "automatic_retry": False,
    }
    if model_summary["finish_reason"] != "stop":
        raise AssetizeError(
            f"Model did not finish normally: {model_summary['finish_reason']}"
        )
    return groups, model_summary


def local_only_groups(candidates: list[dict]) -> tuple[list[dict], dict]:
    groups = [
        {
            "candidate_ids": [candidate["id"]],
            "purpose": "分镜",
            "content": (
                f"{compact_timestamp(candidate['start_ms'])}-"
                f"{compact_timestamp(candidate['end_ms'])}"
            ),
            "cover_candidate_id": candidate["id"],
            "confidence": None,
            "confidence_type": "not_applicable",
        }
        for candidate in candidates
    ]
    return groups, {
        "mode": "local",
        "provider": None,
        "usage": {
            "available": True,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
        },
    }


def selected_mode(args: argparse.Namespace) -> str:
    if getattr(args, "local_only", False):
        return "local"
    return str(getattr(args, "mode", "local"))


def transcode_clip(
    source: Path,
    destination: Path,
    segment: dict,
    ffmpeg: str,
    timeout_seconds: int = DEFAULT_MEDIA_TIMEOUT_SECONDS,
) -> None:
    start_seconds = segment["start_ms"] / 1000
    duration_seconds = segment["duration_ms"] / 1000
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ss",
            f"{start_seconds:.6f}",
            "-t",
            f"{duration_seconds:.6f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-sn",
            "-dn",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            "-avoid_negative_ts",
            "make_zero",
            "-map_metadata",
            "-1",
            "-y",
            str(destination),
        ],
        f"rendering segment {segment['index']:03d}",
        timeout=timeout_seconds,
    )


def extract_keyframe(
    source: Path,
    destination: Path,
    timestamp_ms: int,
    ffmpeg: str,
    timeout_seconds: int = DEFAULT_MEDIA_TIMEOUT_SECONDS,
) -> None:
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{timestamp_ms / 1000:.6f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(destination),
        ],
        f"extracting keyframe at {timestamp_ms}ms",
        timeout=timeout_seconds,
    )


def create_overview(
    keyframe_dir: Path,
    destination: Path,
    ffmpeg: str,
    timeout_seconds: int = DEFAULT_MEDIA_TIMEOUT_SECONDS,
) -> None:
    keyframes = sorted(keyframe_dir.glob("[0-9][0-9][0-9]_*.jpg"))
    if not keyframes:
        raise AssetizeError("No numbered keyframes found for the overview")
    columns = min(5, len(keyframes))
    rows = math.ceil(len(keyframes) / columns)
    filter_graph = (
        "scale=240:426:force_original_aspect_ratio=decrease,"
        "pad=240:426:(ow-iw)/2:(oh-ih)/2:color=white,"
        f"tile={columns}x{rows}:nb_frames={len(keyframes)}:padding=6:margin=6:color=white"
    )
    run_command(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-pattern_type",
            "glob",
            "-framerate",
            "1",
            "-i",
            str(keyframe_dir / "[0-9][0-9][0-9]_*.jpg"),
            "-vf",
            filter_graph,
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-y",
            str(destination),
        ],
        "creating the shot overview",
        timeout=timeout_seconds,
    )


def move_existing_assets_to_backup(package_root: Path) -> str | None:
    video_dir = package_root / "01_分镜视频"
    keyframe_dir = package_root / "02_关键帧"
    root_overview = package_root / "分镜总览.jpg"
    has_assets = (
        (video_dir.exists() and any(video_dir.iterdir()))
        or (keyframe_dir.exists() and any(keyframe_dir.iterdir()))
        or root_overview.exists()
    )
    if not has_assets:
        for directory in (video_dir, keyframe_dir):
            if directory.exists():
                directory.rmdir()
        return None

    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d-%H%M%S")
    backup_dir = package_root / "03_索引记录" / f"备份-render-{timestamp}"
    if backup_dir.exists():
        backup_dir = backup_dir.with_name(f"{backup_dir.name}-{uuid.uuid4().hex[:6]}")
    backup_dir.mkdir(parents=True)
    if video_dir.exists():
        shutil.move(str(video_dir), str(backup_dir / video_dir.name))
    if keyframe_dir.exists():
        shutil.move(str(keyframe_dir), str(backup_dir / keyframe_dir.name))
    if root_overview.exists():
        shutil.move(str(root_overview), str(backup_dir / root_overview.name))
    return str(backup_dir.relative_to(package_root))


def render_package(
    package_root: Path,
    data: dict,
    *,
    ffmpeg: str,
    ffprobe: str,
    render_clips: bool,
    replace_assets: bool,
    timeout_seconds: int = DEFAULT_MEDIA_TIMEOUT_SECONDS,
) -> dict:
    source_relative = data.get("source", {}).get("relative_path", "源视频.mp4")
    source = resolve_package_member(package_root, source_relative)
    if not source.is_file():
        raise FileNotFoundError(f"Source video not found: {source}")
    segments = normalize_and_validate_segments(data)
    data["segments"] = segments

    target_video_dir = package_root / "01_分镜视频"
    target_keyframe_dir = package_root / "02_关键帧"
    targets_have_assets = (
        (target_video_dir.exists() and any(target_video_dir.iterdir()))
        or (target_keyframe_dir.exists() and any(target_keyframe_dir.iterdir()))
        or (package_root / "分镜总览.jpg").exists()
    )
    if targets_have_assets and not replace_assets:
        raise FileExistsError(
            "Rendered assets already exist; use --replace-assets to move them to a backup first"
        )

    work_root = package_root / f".render-work-{uuid.uuid4().hex}"
    work_video_dir = work_root / "01_分镜视频"
    work_keyframe_dir = work_root / "02_关键帧"
    work_video_dir.mkdir(parents=True)
    work_keyframe_dir.mkdir(parents=True)
    clip_probes = []
    try:
        for segment in segments:
            if render_clips:
                clip_path = work_video_dir / f"{segment['file_stem']}.mp4"
                transcode_clip(
                    source,
                    clip_path,
                    segment,
                    ffmpeg,
                    timeout_seconds=timeout_seconds,
                )
                clip_probe = probe_media(
                    clip_path,
                    ffprobe,
                    timeout_seconds=timeout_seconds,
                )
                if not clip_probe["capcut_compatible_encoding_profile"]:
                    raise AssetizeError(
                        "Rendered clip does not match the documented editor-compatible "
                        f"encoding profile: {clip_path.name}"
                    )
                clip_probes.append(
                    {
                        "file": clip_path.name,
                        "duration_ms": clip_probe["duration_ms"],
                        "video_codec": clip_probe["video"]["codec"],
                        "audio_codec": (
                            clip_probe["audio"]["codec"] if clip_probe["audio"] else None
                        ),
                        "capcut_compatible_encoding_profile": True,
                    }
                )
            keyframe_path = work_keyframe_dir / f"{segment['file_stem']}.jpg"
            extract_keyframe(
                source,
                keyframe_path,
                segment["cover_ms"],
                ffmpeg,
                timeout_seconds=timeout_seconds,
            )

        work_overview = work_keyframe_dir / "分镜总览.jpg"
        create_overview(
            work_keyframe_dir,
            work_overview,
            ffmpeg,
            timeout_seconds=timeout_seconds,
        )

        backup_relative = None
        if targets_have_assets:
            backup_relative = move_existing_assets_to_backup(package_root)
        else:
            for directory in (target_video_dir, target_keyframe_dir):
                if directory.exists():
                    directory.rmdir()

        if render_clips:
            shutil.move(str(work_video_dir), str(target_video_dir))
        else:
            target_video_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(work_keyframe_dir), str(target_keyframe_dir))
        shutil.copy2(target_keyframe_dir / "分镜总览.jpg", package_root / "分镜总览.jpg")
        root_hash = sha256_file(package_root / "分镜总览.jpg")
        keyframe_hash = sha256_file(target_keyframe_dir / "分镜总览.jpg")
        if root_hash != keyframe_hash:
            raise AssetizeError("The two overview images are not identical")
    finally:
        if work_root.exists():
            shutil.rmtree(work_root)

    return {
        "render_clips": render_clips,
        "clip_count": len(clip_probes),
        "keyframe_count": len(segments),
        "overview_sha256": root_hash,
        "backup": backup_relative,
        "clips": clip_probes,
    }


def is_tiktok_input(value: str) -> bool:
    try:
        validate_tiktok_url(value)
        return True
    except ValueError:
        return False


def build_preflight_report(args: argparse.Namespace) -> dict:
    return build_preflight_report_impl(
        args,
        is_tiktok_input=is_tiktok_input,
        selected_mode=selected_mode,
        load_api_key=load_api_key,
        probe_media=probe_media,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", help="TikTok HTTPS URL or local MP4")
    parser.add_argument("--output-parent", type=Path)
    parser.add_argument("--api-key-file", type=Path)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT_FILE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--fps", type=float, default=2.0)
    parser.add_argument("--max-tokens", type=int, default=6000)
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument(
        "--media-timeout-seconds",
        type=int,
        default=DEFAULT_MEDIA_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--max-hybrid-upload-mb",
        type=int,
        default=DEFAULT_MAX_HYBRID_UPLOAD_MB,
    )
    parser.add_argument(
        "--max-scene-events",
        type=int,
        default=DEFAULT_MAX_SCENE_EVENTS,
    )
    parser.add_argument(
        "--scene-threshold",
        type=parse_scene_threshold,
        default=DEFAULT_SCENE_THRESHOLD,
        help="Scene-change sensitivity threshold, or auto for local pre-scan tuning.",
    )
    parser.add_argument(
        "--min-shot-seconds",
        type=float,
        default=None,
        help=(
            "Minimum shot length. Defaults to 0.60 for manual thresholds; "
            "auto mode chooses a material-aware value unless this is set."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=("local", "hybrid"),
        default="local",
        help=(
            "local: zero-token physical shots (default); hybrid: keep the same "
            "shot boundaries and use one model call only for semantic labels"
        ),
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Deprecated compatibility alias for --mode local.",
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--render-only", type=Path)
    parser.add_argument(
        "--reuse-download-summary",
        type=Path,
        help="Reuse verified TikTok metadata for an already downloaded local MP4.",
    )
    parser.add_argument("--replace-assets", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    authentication = parser.add_mutually_exclusive_group()
    authentication.add_argument("--cookies-from-browser")
    authentication.add_argument("--cookies-file", type=Path)
    return parser


def validate_arguments(args: argparse.Namespace) -> None:
    if (
        args.fps <= 0
        or args.max_tokens <= 0
        or args.timeout_seconds <= 0
        or args.media_timeout_seconds <= 0
        or args.max_hybrid_upload_mb <= 0
        or args.max_scene_events <= 0
    ):
        raise ValueError("Numeric limits and timeouts must be positive")
    if args.render_only:
        if (
            args.input
            or args.output_parent
            or args.plan_only
            or args.local_only
            or args.reuse_download_summary
        ):
            raise ValueError(
                "--render-only cannot be combined with input, --output-parent, --plan-only, --local-only, or --reuse-download-summary"
            )
        return
    if args.local_only and args.mode == "hybrid":
        raise ValueError("--local-only cannot be combined with --mode hybrid")
    if not args.input:
        raise ValueError("Provide a TikTok URL or local MP4")
    if not args.output_parent:
        raise ValueError("--output-parent is required")
    if args.scene_threshold != "auto" and not 0.01 <= args.scene_threshold <= 1:
        raise ValueError("--scene-threshold must be between 0.01 and 1, or auto")
    if args.min_shot_seconds is not None and not 0.1 <= args.min_shot_seconds <= 10:
        raise ValueError("--min-shot-seconds must be between 0.1 and 10")
    if args.replace_assets:
        raise ValueError("--replace-assets is only valid with --render-only")
    if args.reuse_download_summary and is_tiktok_input(args.input):
        raise ValueError(
            "--reuse-download-summary requires a local MP4 input, not a TikTok URL"
        )
    if args.reuse_download_summary and (
        args.cookies_from_browser or args.cookies_file
    ):
        raise ValueError("Cookie options are not used when reusing a local download")


def run_render_only(args: argparse.Namespace, ffmpeg: str, ffprobe: str) -> dict:
    segments_path = args.render_only.expanduser().resolve()
    if not segments_path.is_file():
        raise FileNotFoundError(f"segments.json not found: {segments_path}")
    if segments_path.parent.name != "03_索引记录":
        raise ValueError("--render-only must point to 03_索引记录/segments.json")
    package_root = segments_path.parent.parent
    data = json.loads(segments_path.read_text(encoding="utf-8-sig"))
    data["segments"] = normalize_and_validate_segments(data)
    render_summary = render_package(
        package_root,
        data,
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        render_clips=True,
        replace_assets=args.replace_assets,
        timeout_seconds=args.media_timeout_seconds,
    )
    write_json(segments_path, data)
    write_manifest_csv(
        package_root / "03_索引记录" / "分镜资产清单.csv", data["segments"]
    )
    run_summary_path = package_root / "03_索引记录" / "run-summary.json"
    if run_summary_path.is_file():
        run_summary = json.loads(run_summary_path.read_text(encoding="utf-8-sig"))
    else:
        run_summary = {"schema_version": 2, "skill_version": SKILL_VERSION}
    run_summary.update(
        {
            "schema_version": 2,
            "skill_version": SKILL_VERSION,
            "status": "complete",
            "stage": "finalize",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "render": render_summary,
            "validation": {
                "timeline_contiguous": True,
                "timeline_coverage_ms": data["source"]["duration_ms"],
                "overview_files_identical": True,
            },
        }
    )
    write_json(run_summary_path, run_summary)
    result = {
        "status": "complete",
        "mode": "render-only",
        "output_dir": str(package_root),
        "render": render_summary,
    }
    return result


def run_new_package(args: argparse.Namespace, ffmpeg: str, ffprobe: str) -> dict:
    output_parent = args.output_parent.expanduser().resolve()
    output_parent.mkdir(parents=True, exist_ok=True)
    input_value = args.input
    download_work = output_parent / f".tiktok-download-{uuid.uuid4().hex}"
    partial_root: Path | None = None
    index_dir: Path | None = None
    stage = "input"
    try:
        if is_tiktok_input(input_value):
            stage = "download"
            download_work.mkdir()
            source_input, download_summary = download_tiktok(
                input_value,
                download_work,
                cookies_from_browser=args.cookies_from_browser,
                cookies_file=args.cookies_file,
                timeout_seconds=args.media_timeout_seconds,
            )
            video_id = str(download_summary["video"]["id"])
            title = str(download_summary["video"].get("title") or "TikTok视频")
        else:
            source_input = Path(input_value).expanduser().resolve()
            if not source_input.is_file():
                raise FileNotFoundError(f"Input video not found: {source_input}")
            if source_input.suffix.lower() != ".mp4":
                raise ValueError("Local input must be an MP4 file")
            if args.reuse_download_summary:
                video_id, title, download_summary = reused_download_summary(
                    args.reuse_download_summary, source_input
                )
            else:
                video_id, title, download_summary = local_input_summary(source_input)

        mode = selected_mode(args)
        stage = "resource_preflight"
        resources = resource_preflight(
            source_input,
            output_parent,
            render_clips=not args.plan_only,
            hybrid_mode=mode == "hybrid",
            max_hybrid_upload_mb=args.max_hybrid_upload_mb,
        )
        run_date = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        final_root = output_parent / package_name(title, video_id, run_date)
        partial_root = output_parent / f".{final_root.name}.partial"
        if final_root.exists() or partial_root.exists():
            raise FileExistsError(
                f"Output already exists: {final_root if final_root.exists() else partial_root}"
            )

        partial_root.mkdir()
        index_dir = partial_root / "03_索引记录"
        (partial_root / "01_分镜视频").mkdir()
        (partial_root / "02_关键帧").mkdir()
        index_dir.mkdir()
        write_stage_summary(index_dir, status="running", stage="copy_source")
        source = partial_root / "源视频.mp4"
        shutil.copy2(source_input, source)
        stage = "probe"
        source_probe = probe_media(
            source,
            ffprobe,
            timeout_seconds=args.media_timeout_seconds,
        )
        source_probe["path"] = "源视频.mp4"
        download_summary = update_download_summary_for_package(download_summary, source)
        download_summary["video"]["probe"] = source_probe
        write_json(index_dir / "download-summary.json", download_summary)
        write_stage_summary(index_dir, status="running", stage=stage)

        stage = "detect"
        auto_tuning = None
        if args.scene_threshold == "auto":
            scanned_events = collect_scene_events(
                source,
                ffmpeg,
                scan_threshold=AUTO_SCAN_THRESHOLD,
                timeout_seconds=args.media_timeout_seconds,
            )
            auto_plan = build_auto_tuning_plan(
                scanned_events,
                source_probe["duration_ms"],
                max_events=args.max_scene_events,
                min_shot_seconds_override=args.min_shot_seconds,
            )
            events = auto_plan["events"]
            detection_statistics = auto_plan["statistics"]
            candidates = auto_plan["candidates"]
            scene_threshold = auto_plan["scene_threshold"]
            min_shot_ms = auto_plan["min_shot_ms"]
            auto_tuning = detection_statistics["auto_tuning"]
        else:
            scene_threshold = float(args.scene_threshold)
            min_shot_seconds = resolved_min_shot_seconds(args.min_shot_seconds)
            min_shot_ms = round(min_shot_seconds * 1000)
            events, detection_statistics = detect_scene_events(
                source,
                ffmpeg,
                scene_threshold=scene_threshold,
                max_events=args.max_scene_events,
                timeout_seconds=args.media_timeout_seconds,
            )
            candidates = build_candidate_shots(
                events,
                source_probe["duration_ms"],
                min_shot_ms=min_shot_ms,
            )
        detection = {
            "method": "ffmpeg_scene_score",
            "scene_threshold": scene_threshold,
            "scene_threshold_source": "auto" if auto_tuning else "manual",
            "scene_threshold_requested": args.scene_threshold,
            "minimum_candidate_duration_ms": min_shot_ms,
            **detection_statistics,
            "candidate_count": len(candidates),
        }
        quality = build_quality_summary(
            candidates,
            source_probe["duration_ms"],
            detection,
        )
        estimated_model_output_tokens = 200 + len(candidates) * 45
        resources["estimated_model_output_tokens"] = (
            estimated_model_output_tokens if mode == "hybrid" else 0
        )
        if mode == "hybrid" and estimated_model_output_tokens > args.max_tokens:
            raise AssetizeError(
                "Hybrid labeling is likely to exceed --max-tokens: "
                f"estimated {estimated_model_output_tokens}, configured {args.max_tokens}"
            )
        write_json(
            index_dir / "candidates.json",
            {
                "schema_version": 1,
                "source_sha256": sha256_file(source),
                "detection": detection,
                **({"auto_tuning": auto_tuning} if auto_tuning else {}),
                "quality": quality,
                "candidates": candidates,
            },
        )
        write_stage_summary(index_dir, status="running", stage=stage)

        stage = "label"
        if mode == "local":
            groups, model_summary = local_only_groups(candidates)
        else:
            groups, model_summary = call_semantic_model(
                source,
                candidates,
                title=title,
                api_key_file=args.api_key_file,
                prompt_file=args.prompt_file,
                model=args.model,
                fps=args.fps,
                max_tokens=args.max_tokens,
                timeout_seconds=args.timeout_seconds,
            )
        write_stage_summary(index_dir, status="running", stage=stage)

        stage = "plan"
        segments = build_final_segments(groups, candidates)
        segments_data = {
            "schema_version": 2,
            "skill_version": SKILL_VERSION,
            "package": {
                "name": final_root.name,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "timezone": "Asia/Shanghai",
            },
            "source": {
                "relative_path": "源视频.mp4",
                "video_id": video_id,
                "title": title,
                "source_url": sanitize_source_url(download_summary.get("source_url")),
                "sha256": sha256_file(source),
                "duration_ms": source_probe["duration_ms"],
                "probe": source_probe,
            },
            "detection": detection,
            "quality": quality,
            "resources": resources,
            "candidates": candidates,
            "model": model_summary,
            **({"auto_tuning": auto_tuning} if auto_tuning else {}),
            "segments": segments,
        }
        segments_data["segments"] = normalize_and_validate_segments(segments_data)
        write_json(index_dir / "segments.json", segments_data)
        write_manifest_csv(index_dir / "分镜资产清单.csv", segments_data["segments"])
        write_stage_summary(index_dir, status="running", stage=stage)

        stage = "render"
        render_summary = render_package(
            partial_root,
            segments_data,
            ffmpeg=ffmpeg,
            ffprobe=ffprobe,
            render_clips=not args.plan_only,
            replace_assets=False,
            timeout_seconds=args.media_timeout_seconds,
        )
        write_json(index_dir / "segments.json", segments_data)
        write_stage_summary(index_dir, status="running", stage=stage)

        stage = "finalize"
        run_summary = {
            "schema_version": 2,
            "skill_version": SKILL_VERSION,
            "status": "planned" if args.plan_only else "complete",
            "stage": stage,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "recoverable": False,
            "next_action": None,
            "input": {
                "type": download_summary["source_type"],
                "source_url": sanitize_source_url(download_summary.get("source_url")),
            },
            "output": {"package_name": final_root.name},
            "tools": {
                "python": sys.version.split()[0],
                "ffmpeg": tool_version(ffmpeg),
                "ffprobe": tool_version(ffprobe),
                "yt_dlp": (
                    download_summary.get("download", {}).get("version")
                    if download_summary.get("download")
                    else None
                ),
            },
            "source": {
                "duration_ms": source_probe["duration_ms"],
                "capcut_compatible_encoding_profile": source_probe[
                    "capcut_compatible_encoding_profile"
                ],
                "video_codec": source_probe["video"]["codec"],
                "audio_codec": (
                    source_probe["audio"]["codec"] if source_probe["audio"] else None
                ),
            },
            "detection": segments_data["detection"],
            **({"auto_tuning": auto_tuning} if auto_tuning else {}),
            "quality": quality,
            "resources": resources,
            "model": model_summary,
            "render": render_summary,
            "validation": {
                "timeline_contiguous": True,
                "timeline_coverage_ms": source_probe["duration_ms"],
                "overview_files_identical": True,
                "segment_count": len(segments_data["segments"]),
            },
        }
        write_json(index_dir / "run-summary.json", run_summary)
        os.replace(partial_root, final_root)

        result = {
            "status": run_summary["status"],
            "mode": mode,
            "output_dir": str(final_root),
            "video_id": video_id,
            "candidate_count": len(candidates),
            "segment_count": len(segments_data["segments"]),
            "scene_threshold": scene_threshold,
            "minimum_candidate_duration_ms": min_shot_ms,
            **({"auto_tuning": auto_tuning} if auto_tuning else {}),
            "usage": model_summary.get("usage", {}),
            "quality": quality,
        }
        return result
    except Exception as exc:
        if (
            isinstance(partial_root, Path)
            and partial_root.exists()
            and isinstance(index_dir, Path)
        ):
            segments_path = index_dir / "segments.json"
            recoverable = segments_path.is_file()
            next_action = (
                "Inspect the partial package, then use --render-only "
                "03_索引记录/segments.json --replace-assets"
                if recoverable
                else "Inspect run-summary.json and candidates.json if present before retrying"
            )
            try:
                write_stage_summary(
                    index_dir,
                    status="failed",
                    stage=stage,
                    error=exc,
                    recoverable=recoverable,
                    next_action=next_action,
                )
            except OSError:
                pass
        raise
    finally:
        if download_work.exists():
            shutil.rmtree(download_work)


def main() -> int:
    args = build_parser().parse_args()
    validate_arguments(args)
    if args.validate_only:
        payload = build_preflight_report(args)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["status"] == "valid" else 2
    ffmpeg, ffprobe = ffmpeg_tools()
    if args.render_only:
        result = run_render_only(args, ffmpeg, ffprobe)
    else:
        result = run_new_package(args, ffmpeg, ffprobe)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def error_code(error: BaseException) -> str:
    if isinstance(error, FileNotFoundError):
        return "INPUT_NOT_FOUND"
    if isinstance(error, FileExistsError):
        return "OUTPUT_EXISTS"
    if isinstance(error, subprocess.TimeoutExpired):
        return "TIMEOUT"
    if isinstance(error, json.JSONDecodeError):
        return "INVALID_JSON"
    if isinstance(error, ValueError):
        return "INVALID_ARGUMENT"
    return "WORKFLOW_FAILED"


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        AssetizeError,
        FileNotFoundError,
        FileExistsError,
        json.JSONDecodeError,
        ValueError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": {
                        "code": error_code(exc),
                        "type": type(exc).__name__,
                        "message": redact_sensitive_text(str(exc)),
                    },
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
