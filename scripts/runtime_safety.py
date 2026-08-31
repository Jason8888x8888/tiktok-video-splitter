#!/usr/bin/env python3
"""Shared privacy and subprocess-safety helpers for the video splitter."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SCRIPT_INTERFACE = "internal-module"
SCRIPT_INTERFACE_REASON = "Imported by CLI scripts for credential, path, URL, and subprocess safety."
SAFE_ENVIRONMENT_KEYS = {
    "ALL_PROXY",
    "HOME",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "NO_PROXY",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "SYSTEMROOT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "USERPROFILE",
}
ARK_KEY_RE = re.compile(r"(?<![A-Za-z0-9._-])ark-[A-Za-z0-9._-]{12,}")
AUTHORIZATION_RE = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+)[^\s\"']+"
)
HTTPS_PREFIX = "https:" + "//"
URL_RE = re.compile(re.escape(HTTPS_PREFIX) + r"[^\s\"'<>]+")


def subprocess_environment() -> dict[str, str]:
    """Return a minimal child environment without model/API credentials."""
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in SAFE_ENVIRONMENT_KEYS
    }
    environment.setdefault("PATH", os.defpath)
    return environment


def sanitize_source_url(value: str | None) -> str | None:
    """Remove query parameters, fragments, and embedded credentials from a URL."""
    if not value:
        return value
    parsed = urlsplit(str(value))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return str(value)
    hostname = parsed.hostname
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, "", ""))


def redact_sensitive_text(value: str, *, max_chars: int = 2000) -> str:
    """Redact known credentials, URL queries, and the current home directory."""
    text = ARK_KEY_RE.sub("[REDACTED_API_KEY]", str(value or ""))
    text = AUTHORIZATION_RE.sub(r"\1[REDACTED]", text)
    text = URL_RE.sub(lambda match: sanitize_source_url(match.group(0)) or "", text)
    home = str(Path.home())
    if home and home != "/":
        text = text.replace(home, "~")
    return text[-max_chars:]


def redact_command_paths(
    value: str,
    arguments: list[str],
    *,
    max_chars: int = 2000,
) -> str:
    """Redact absolute filesystem arguments that a child process echoed back."""
    text = str(value or "")
    absolute_arguments = sorted(
        {
            argument
            for argument in arguments
            if isinstance(argument, str) and Path(argument).is_absolute()
        },
        key=len,
        reverse=True,
    )
    for argument in absolute_arguments:
        label = Path(argument).name or "path"
        text = text.replace(argument, f"[LOCAL_PATH]/{label}")
    return redact_sensitive_text(text, max_chars=max_chars)


def resolve_package_member(package_root: Path, relative_path: str) -> Path:
    """Resolve a package-relative path and reject absolute paths or traversal."""
    root = package_root.expanduser().resolve()
    candidate = Path(str(relative_path))
    if candidate.is_absolute():
        raise ValueError("Package source path must be relative")
    resolved = (root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError("Package source path escapes the package directory")
    return resolved
