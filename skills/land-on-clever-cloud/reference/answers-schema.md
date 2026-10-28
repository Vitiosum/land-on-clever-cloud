# answers.json — the developer's replies to the questions

Written by the agent into `.clever-landing/answers.json` (untracked, git-excluded) after screen 2, read with `python3 -I scripts/questions.py --scan .clever-landing/scan.json --answers .clever-landing/answers.json`. Every key is optional. A missing key means "not asked or not answered yet": the default below applies and is shown as `[assumed]`. `null` means "the developer does not know": same result, and the question is not asked again.

An unknown key, a value outside the table, a number that is negative or not finite, or a file that is not JSON is refused with exit code 2. A committed `.clever-landing/` is refused with exit code 3.

Questions are numbered as they are asked: `questions.py` numbers only the questions still open, in the order health, production, load, service (then `ops` after the POC). With health answered and no doubt on production, the load question is question 1.

| Key | Allowed values | Default when absent or `null` | Asked in |
|---|---|---|---|
| `health` | `"yes"`, `"no"`, `"unsure"`, `null` | `"unsure"` (the safe side), `[assumed]` | `health` |
| `hds_contract_signed` | `"yes"`, `"no"`, `null` | `"no"` when health is not `"no"`, `[assumed]` | sub-question of `health`, only after `"yes"` or `"unsure"` |
| `data_gb` | number >= 0, or `null` | unknown: the sizing states the figure it assumes | `production`, asked only when the scan doubts what runs in production |
| `files_gb` | number >= 0, or `null` | unknown: same | `production` (same) |
| `declared_plans` | list of strings (20 at most, 200 characters each) | `[]`: the existing cost is "not estimated" | `production` (same) |
| `load` | object, see below | `shape` `"steady"`, `[assumed]` | `load` |
| `service` | object, see below | `{"rpo_hours": 24, "downtime": "hours", "h24": false}`, `[assumed]` | `service` |
| `after_poc` | object, see below | `{}` | `ops`, after the POC only |

`load` keys: `peak_rps` (number >= 0 or `null`), `shape` (`"steady"`, `"predictable_peaks"`, `"events"` or `null`), `reachable_between_peaks` (`true`, `false` or `null`).

`service` keys: `rpo_hours` (number >= 0, acceptable data loss in hours), `downtime` (`"minutes"`, `"hours"`, `"day"`), `h24` (`true` or `false`); each may be `null`. A missing or `null` key takes its default, `service` is named in `assumed` and the key in `service_assumed`: the price screen names only those keys as assumed.

`after_poc` keys: `operator`, `dns`, `deadline`, `legal_entity` (each a string of 200 characters at most, or `null`).

## What the questions give back

`effective_answers(scan, answers)` returns the answers completed with the defaults above and `assumed`, the list of field names that are a default or unknown (`health`, `hds_contract_signed`, `data_gb`, `files_gb`, `load`, `service`). `data_gb` and `files_gb` are named only when the scan shows a database or files. Screens label those fields `[assumed]`; nothing is filled in silently.

## Example

```json
{
  "health": "yes",
  "hds_contract_signed": "no",
  "data_gb": 9.3,
  "files_gb": null,
  "declared_plans": ["render web: standard", "render postgres: 4 GB"],
  "load": {"peak_rps": 40, "shape": "predictable_peaks", "reachable_between_peaks": false},
  "service": {"rpo_hours": 1, "downtime": "minutes", "h24": false}
}
```
