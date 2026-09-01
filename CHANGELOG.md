# Changelog

All notable changes follow semantic versioning.

## [0.1.0-beta.3] - 2026-09-01

### Added

- Public installation, English quick start, responsible-use notes, and an output-directory screenshot.
- A real FFmpeg end-to-end regression, auto-tuning JSON Schema coverage, trigger cases, output evaluations, and governed release metadata.
- GitHub issue and pull request templates.

### Changed

- Automatic tuning now uses transition density instead of candidate-shot density, so a baseline shot is not miscounted as a transition.
- CI disables Python bytecode generation, verifies FFmpeg, and pins third-party Actions to immutable commits.
- Compatibility metadata now describes a remote Agent Skills package executed through Bash.

### Fixed

- A short video with no detected scene events is now classified as `稳定产品展示` instead of `口播/教程演示`.

### Security

- Public release documentation now states authorization, platform-policy, privacy, trademark, and non-affiliation boundaries.

## [0.1.0-beta.2] - 2026-09-01

### Added

- Automatic local sensitivity tuning with `--scene-threshold auto`.
- Auto-tuning evidence in `run-summary.json`, `segments.json`, and `candidates.json`.

### Changed

- The default scene threshold is now `auto` instead of fixed `0.20`.
- Local objective shot names now use `分镜`, for example `001_分镜_00m00s-00m03s`.

## [0.1.0-beta.1] - 2026-08-31

### Added

- Read-only capability-matrix preflight with meaningful exit status.
- Sanitized child-process environment and yt-dlp user-config isolation.
- URL, credential, path, and external-error redaction.
- Scene-event truncation evidence and quality review signals.
- Hybrid upload, output-token, disk-space, and command-timeout guards.
- Failure-stage summaries and render recovery guidance.
- Versioned JSON schemas, canonical interface metadata, CI, and release checks.

### Changed

- Local semantic confidence is now not applicable instead of a misleading 1.0.
- Missing provider Token usage is reported as unknown instead of zero.
- Output language now promises a CapCut-compatible encoding profile, not verified editor import.

### Security

- Package source paths can no longer escape the package root during render-only runs.
- Ark credentials are removed from external child-process environments.
