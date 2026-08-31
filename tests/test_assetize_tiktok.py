from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import assetize_tiktok as assetizer
import download_tiktok as downloader
import runtime_safety


class NamingTests(unittest.TestCase):
    def test_package_name_contains_sanitized_title_id_and_date(self) -> None:
        result = assetizer.package_name(
            '  测试/视频："新品"  ', "7670797411941420301", "2026-08-25"
        )
        self.assertEqual(
            result,
            "视频拆解-测试_视频_新品-7670797411941420301-2026-08-25",
        )

    def test_segment_stem_is_ordered_and_safe(self) -> None:
        self.assertEqual(
            assetizer.segment_stem(3, "商品露出", "GW/Shark 清洁剂"),
            "003_商品露出_GW_Shark_清洁剂",
        )


class CandidateTests(unittest.TestCase):
    def test_short_candidate_removes_weaker_boundary(self) -> None:
        events = [
            {"time_ms": 1000, "score": 0.8},
            {"time_ms": 1400, "score": 0.3},
            {"time_ms": 3000, "score": 0.9},
        ]
        candidates = assetizer.build_candidate_shots(
            events, 5000, min_shot_ms=600
        )
        self.assertEqual(
            [(item["start_ms"], item["end_ms"]) for item in candidates],
            [(0, 1000), (1000, 3000), (3000, 5000)],
        )

    def test_scene_detection_reports_truncation(self) -> None:
        stdout = "\n".join(
            [
                f"frame:{index} pts_time:{index}.0\nlavfi.scene_score=0.{index + 5}"
                for index in range(1, 5)
            ]
        )
        completed = SimpleNamespace(stdout=stdout)
        with mock.patch.object(assetizer, "run_command", return_value=completed):
            events, statistics = assetizer.detect_scene_events(
                Path("video.mp4"),
                "ffmpeg",
                scene_threshold=0.2,
                max_events=2,
            )
        self.assertEqual(len(events), 2)
        self.assertEqual(statistics["raw_event_count"], 4)
        self.assertEqual(statistics["clustered_event_count"], 4)
        self.assertTrue(statistics["events_truncated"])


class PlanTests(unittest.TestCase):
    def test_model_plan_must_cover_candidates_once_in_order(self) -> None:
        plan = {
            "segments": [
                {
                    "candidate_ids": [1],
                    "purpose": "开场钩子",
                    "content": "湿滑险摔",
                    "cover_candidate_id": 1,
                    "confidence": 0.9,
                },
                {
                    "candidate_ids": [2],
                    "purpose": "痛点展示",
                    "content": "楼梯青苔",
                    "cover_candidate_id": 2,
                    "confidence": 0.85,
                },
                {
                    "candidate_ids": [3],
                    "purpose": "商品露出",
                    "content": "展示清洁剂",
                    "cover_candidate_id": 3,
                    "confidence": 0.8,
                },
            ]
        }
        normalized = assetizer.validate_grouped_plan(plan, 3)
        self.assertEqual(len(normalized), 3)

    def test_model_plan_rejects_merged_physical_shots(self) -> None:
        plan = {
            "segments": [
                {
                    "candidate_ids": [1, 2],
                    "purpose": "开场钩子",
                    "content": "连续动作",
                    "cover_candidate_id": 1,
                    "confidence": 0.9,
                }
            ]
        }
        with self.assertRaisesRegex(assetizer.AssetizeError, "cannot merge"):
            assetizer.validate_semantic_plan(plan, 2)

    def test_model_plan_rejects_missing_candidate(self) -> None:
        plan = {
            "segments": [
                {
                    "candidate_ids": [1, 3],
                    "purpose": "开场钩子",
                    "content": "错误分组",
                    "cover_candidate_id": 1,
                    "confidence": 0.9,
                }
            ]
        }
        with self.assertRaises(assetizer.AssetizeError):
            assetizer.validate_grouped_plan(plan, 3)


class TimelineTests(unittest.TestCase):
    def test_timeline_must_be_contiguous_and_complete(self) -> None:
        data = {
            "source": {"duration_ms": 5000},
            "segments": [
                {
                    "start_ms": 0,
                    "end_ms": 2000,
                    "purpose": "开场钩子",
                    "content": "湿滑险摔",
                },
                {
                    "start_ms": 2000,
                    "end_ms": 5000,
                    "purpose": "效果证明",
                    "content": "地面变干净",
                },
            ],
        }
        segments = assetizer.normalize_and_validate_segments(data)
        self.assertEqual(segments[-1]["end_ms"], 5000)
        self.assertEqual(segments[0]["clip"], "01_分镜视频/001_开场钩子_湿滑险摔.mp4")

    def test_timeline_rejects_gap(self) -> None:
        data = {
            "source": {"duration_ms": 5000},
            "segments": [
                {"start_ms": 0, "end_ms": 2000},
                {"start_ms": 2100, "end_ms": 5000},
            ],
        }
        with self.assertRaises(assetizer.AssetizeError):
            assetizer.normalize_and_validate_segments(data)


class ReusedDownloadTests(unittest.TestCase):
    def test_reused_summary_preserves_tiktok_id_after_hash_verification(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            source = temp_path / "source.mp4"
            source.write_bytes(b"test-video-bytes")
            summary_path = temp_path / "download-summary.json"
            summary_path.write_text(
                json.dumps(
                    {
                        "source_type": "tiktok_url",
                        "source_url": "https://www.tiktok.com/@tester/video/123",
                        "video": {
                            "id": "123",
                            "title": "Test title",
                            "sha256": assetizer.sha256_file(source),
                        },
                        "download": {"tool": "yt-dlp"},
                    }
                ),
                encoding="utf-8",
            )
            video_id, title, summary = assetizer.reused_download_summary(
                summary_path, source
            )
            self.assertEqual(video_id, "123")
            self.assertEqual(title, "Test title")
            self.assertTrue(summary["reuse"]["sha256_verified"])


class ModeTests(unittest.TestCase):
    def test_default_mode_is_local_and_zero_token(self) -> None:
        args = assetizer.build_parser().parse_args(
            ["video.mp4", "--output-parent", "/tmp/output"]
        )
        self.assertEqual(assetizer.selected_mode(args), "local")
        self.assertAlmostEqual(args.scene_threshold, 0.20)

    def test_local_groups_use_objective_time_range_names(self) -> None:
        groups, summary = assetizer.local_only_groups(
            [
                {
                    "id": 1,
                    "start_ms": 0,
                    "end_ms": 4230,
                    "duration_ms": 4230,
                }
            ]
        )
        self.assertEqual(groups[0]["purpose"], "物理分镜")
        self.assertEqual(groups[0]["content"], "00m00s-00m04s")
        self.assertIsNone(groups[0]["confidence"])
        self.assertEqual(summary["mode"], "local")
        self.assertEqual(summary["usage"]["total_tokens"], 0)

    def test_deprecated_local_alias_conflicts_with_hybrid(self) -> None:
        args = assetizer.build_parser().parse_args(
            [
                "video.mp4",
                "--output-parent",
                "/tmp/output",
                "--mode",
                "hybrid",
                "--local-only",
            ]
        )
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            assetizer.validate_arguments(args)


class PrivacyAndSafetyTests(unittest.TestCase):
    def test_subprocess_environment_excludes_api_key(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"PATH": "/usr/bin", "ARK_API_KEY": "ark-secret-value-1234567890"},
            clear=True,
        ):
            environment = runtime_safety.subprocess_environment()
        self.assertEqual(environment["PATH"], "/usr/bin")
        self.assertNotIn("ARK_API_KEY", environment)

    def test_source_url_drops_query_fragment_and_credentials(self) -> None:
        sanitized = runtime_safety.sanitize_source_url(
            "https://user:pass@www.tiktok.com/@a/video/123?utm_source=x#fragment"
        )
        self.assertEqual(sanitized, "https://www.tiktok.com/@a/video/123")

    def test_render_source_cannot_escape_package(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "escapes"):
                runtime_safety.resolve_package_member(
                    Path(temp_dir), "../../private.mp4"
                )

    def test_child_process_paths_are_redacted(self) -> None:
        detail = "/private/tmp/job/source.mp4: Invalid data"
        redacted = runtime_safety.redact_command_paths(
            detail,
            ["ffprobe", "/private/tmp/job/source.mp4"],
        )
        self.assertEqual(redacted, "[LOCAL_PATH]/source.mp4: Invalid data")

    def test_missing_usage_is_unknown_not_zero(self) -> None:
        usage = assetizer.normalized_usage({})
        self.assertFalse(usage["available"])
        self.assertIsNone(usage["total_tokens"])

    def test_yt_dlp_uses_ignore_config_and_sanitized_environment(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="2026.01.01\n", stderr="")
        with mock.patch.object(
            downloader.subprocess, "run", return_value=completed
        ) as run_mock:
            downloader.run_yt_dlp(
                ["yt-dlp", "--ignore-config", "--version"],
                "reading version",
            )
        arguments, keywords = run_mock.call_args
        self.assertIn("--ignore-config", arguments[0])
        self.assertNotIn("ARK_API_KEY", keywords["env"])


class PreflightTests(unittest.TestCase):
    def test_preflight_rejects_missing_local_input(self) -> None:
        args = assetizer.build_parser().parse_args(
            [
                "/definitely/missing/video.mp4",
                "--output-parent",
                "/tmp/output",
                "--validate-only",
            ]
        )
        report = assetizer.build_preflight_report(args)
        self.assertEqual(report["status"], "invalid")
        input_check = next(item for item in report["checks"] if item["name"] == "input")
        self.assertFalse(input_check["ok"])

    def test_hybrid_preflight_requires_credentials_without_calling_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            video = Path(temp_dir) / "video.mp4"
            video.write_bytes(b"not-probed-during-preflight")
            args = assetizer.build_parser().parse_args(
                [
                    str(video),
                    "--output-parent",
                    temp_dir,
                    "--mode",
                    "hybrid",
                    "--validate-only",
                ]
            )
            with mock.patch.dict(
                os.environ,
                {"PATH": os.environ.get("PATH", "")},
                clear=True,
            ):
                report = assetizer.build_preflight_report(args)
        self.assertEqual(report["status"], "invalid")
        credential_check = next(
            item for item in report["checks"] if item["name"] == "ark_credentials"
        )
        self.assertFalse(credential_check["ok"])
        self.assertFalse(report["api_called"])


if __name__ == "__main__":
    unittest.main()
