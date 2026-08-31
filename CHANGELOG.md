# Changelog

All notable changes follow semantic versioning.

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
