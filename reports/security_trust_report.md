# Security Trust Report

- OK: `True`
- Scanned files: `24`
- Scripts: `6`
- Internal script modules: `3`
- Secret findings: `0`
- Network-capable scripts: `0`
- Network policy covered scripts: `0`
- Network policy missing scripts: `0`
- File-write scripts: `3`
- Permission approvals: `2 / 2`
- Permission approval gaps: `0`
- CLI help smoke checked: `3`
- CLI help smoke failures: `0`
- Interactive scripts: `0`
- Package hash scope: `source-contract-without-generated-reports`
- Package hash files: `24`
- Package SHA256: `eaa73f5ea01e79b99daeaf5bf106b39cdde2354ba8660f0fed02b6fb468cf26d`

## Failures

- None

## Warnings

- None

## Dependency Evidence

- Files: `requirements.txt`
- Pinned entries: `0`
- Unpinned entries: `0`

## Network Policy

- Policy file: `security/network_policy.json`
- Present: `True`
- Covered scripts: `0`
- Missing scripts: `none`
- Mismatches: `0`

## Permission Governance

- Policy file: `security/permission_policy.json`
- Present: `True`
- Required capabilities: `file_write, subprocess`
- Approved capabilities: `file_write, subprocess`
- Missing approvals: `none`
- Invalid approvals: `none`
- Expired approvals: `none`

## CLI Help Smoke

- Enabled: `True`
- Timeout seconds: `5.0`
- Checked scripts: `3`
- Passed scripts: `3`
- Failed scripts: `none`

## Script Surface

| Script | Interface | Declared | Argparse | Main Guard | Input | Network | File Write | Subprocess | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| scripts/assetize_tiktok.py | cli | False | True | True | False | False | True | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts/download_tiktok.py | cli | False | True | True | False | False | True | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
| scripts/package_contracts.py | internal-module | True | False | False | False | False | True | False | Imported by the CLI for deterministic output contracts and pipeline state. |
| scripts/preflight_checks.py | internal-module | True | False | False | False | False | False | False | Imported by the main CLI to perform read-only capability checks. |
| scripts/runtime_safety.py | internal-module | True | False | False | False | False | False | False | Imported by CLI scripts for credential, path, URL, and subprocess safety. |
| scripts/verify_release.py | cli | False | True | True | False | False | False | True | Default CLI classification; add SCRIPT_INTERFACE for internal modules. |
