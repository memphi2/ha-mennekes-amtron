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

## What a private repository cannot have

Two of those steps need a public repository or a paid plan, and the script
reports them as unavailable instead of failing:

- **Secret scanning and push protection** — GitHub Advanced Security.
- **Branch protection and rulesets on `main`** — GitHub Pro or Team.

The CodeQL workflow is in the same position: its analysis runs, but uploading
the result needs code scanning, so the job is conditional on the repository
being public.

Run the script again after publishing the repository; everything else is
already applied and will stay as it is.

## Why the repository is private

The integration writes to a wallbox, and nothing in it has been measured on
real hardware yet. The on-device verification in
[quickstart.md](quickstart.md) and [safety.md](safety.md) is the step that
closes that gap; publishing before it would invite people to control a
charging station with untested code.
