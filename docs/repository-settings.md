# Repository Settings

Branch protection, secret scanning and the repository toggles live on GitHub,
not in the repository, so they never show up in a diff and are easy to lose.
`scripts/apply_repo_settings.py` declares them instead.

```bash
python scripts/apply_repo_settings.py            # print what would change
python scripts/apply_repo_settings.py --apply    # change it
```

The script is idempotent and needs an authenticated `gh`.

## What it enforces

| Setting | Value |
|---|---|
| Issues | enabled |
| Wiki, Projects | disabled |
| Merge, squash and rebase merges | all allowed |
| Auto-merge, delete branch on merge | disabled |
| Dependabot vulnerability alerts | enabled |
| Secret scanning and push protection | enabled |
| `main`: required status checks | `dependabot`, `hacs`, `hassfest`, `validate (min-ha)`, `validate (current-ha)`, strict |
| `main`: force pushes, deletions | blocked |

`scripts/check_repo.py` verifies that every required status check still names
a job that exists in `.github/workflows/validate.yml`. Without that check,
renaming a CI job would leave branch protection quietly protecting nothing.

## What needs a public repository

Two of those steps need a public repository or a paid plan, and the script
reports them as unavailable instead of failing:

- **Secret scanning and push protection** — GitHub Advanced Security.
- **Branch protection and rulesets on `main`** — GitHub Pro or Team.

The repository is public, so both are active. The CodeQL workflow is in the
same position: its analysis always runs, but uploading the result needs code
scanning, so that step is conditional on the repository being public.

`ruff` carries the same class of checks regardless: it selects the bandit
ruleset `S`, which fails the build on the Python findings CodeQL would
report. CodeQL adds the GitHub Actions analysis and cross-file data flow on
top, which is why the job stays in place rather than being deleted.

## Settings this script does not cover

The repository description and topics are not part of the declared
configuration, because they are prose rather than protection. They are:

- **Description** — `Unofficial Home Assistant integration for MENNEKES AMTRON wallboxes over local Modbus RTU`
- **Topics** — `amtron`, `ev-charging`, `hacs`, `home-assistant`, `homeassistant`, `mennekes`, `modbus`, `modbus-rtu`, `rs485`, `wallbox`

## Not yet verified on hardware

The integration writes to a wallbox and nothing in it has been measured on
real hardware. That is a caveat on the release, not a reason to keep the
source closed: the README and the release notes say so plainly, new entries
start read-only, and the on-device verification in
[quickstart.md](quickstart.md) and [safety.md](safety.md) is the step that
closes the gap.
