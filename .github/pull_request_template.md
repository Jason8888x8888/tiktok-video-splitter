## Summary

Describe the problem, the focused change, and the user-visible result.

## Contract impact

- [ ] No output schema or CLI contract change
- [ ] Output schema and `schema_version` are updated together
- [ ] CLI behavior or defaults changed and are documented
- [ ] Upload, cost, credential, Cookie, download, or platform-access behavior changed

## Verification

- [ ] `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v`
- [ ] `python3 scripts/verify_release.py`
- [ ] New behavior has a focused regression test
- [ ] Fixtures are synthetic or publicly redistributable

## Safety

- [ ] No API keys, cookies, signed URLs, private media, account identifiers, or absolute user paths are included
- [ ] The change does not bypass login, region, CAPTCHA, access control, or platform restrictions
