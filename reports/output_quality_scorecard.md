# Output Quality Scorecard

> Scope: recorded fixtures with deterministic string assertions only. `Gate pass` below means the fixture assertion gate passed; it is not provider-backed quality evidence, a blind holdout, or independent human agreement.

This v0 scorecard compares static without-skill and with-skill outputs using assertion grading.

- Cases: `5`
- Baseline pass rate: `0.0`
- With-skill pass rate: `100.0`
- Delta: `100.0`
- Regressions: `0`
- Blind A/B pairs retained: `0`
- Gate pass: `True`

The public fixture source contains its expected outputs, so it must not be counted as blind-review evidence. Human adjudication remains `missing evidence`.

## Case Results

| Case | Baseline | With Skill | Delta | Winner | Failed With-Skill Assertions |
| --- | ---: | ---: | ---: | --- | --- |
| local_split_contract | 0.0 | 100.0 | 100.0 | with_skill | None |
| hybrid_requires_authorization | 0.0 | 100.0 | 100.0 | with_skill | None |
| download_only_boundary | 0.0 | 100.0 | 100.0 | with_skill | None |
| static_video_auto_tuning | 0.0 | 100.0 | 100.0 | with_skill | None |
| partial_recovery | 0.0 | 100.0 | 100.0 | with_skill | None |

## Failure Taxonomy

- No with-skill assertion failures.

## Next Fixes

- Add holdout cases before using this as a release gate.
- Promote repeated failed assertions into the output-risk profile.
- Keep assertions tied to material deliverables, not phrasing trivia.
