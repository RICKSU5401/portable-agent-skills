# Quota-aware handoff

[繁體中文](README.zh-TW.md) · Public candidate 0.1.0

Save verifiable progress before a budget is exhausted, hand one bounded unit to
an authorized successor, and verify its return at the original conversation.
Python 3.10+ standard library only. No provider credentials, SDK or network calls
are needed for the gate, checkpoint or return inspector.

## What runs

| Helper | Behavior | Does it start a model? |
| --- | --- | --- |
| `scripts/quota_guard.py` | Parse exported usage or evaluate a snapshot against a bounded unit | No |
| `scripts/handoff.py checkpoint/verify` | Freeze explicit files and verify SHA-256 identity | No |
| `scripts/handoff.py inspect-return` | Bind return/origin/task/unit to independently retained source pins | No |
| `scripts/dispatch.py plan` | Check an explicitly configured native CLI profile | No |
| `scripts/dispatch.py launch --execute` | One cooperative child launch, ACK and permit | Yes, only when configured and authorized |

Provider names and models are local configuration, not built-in rankings. The
dispatcher accepts safe profile identifiers and a native executable with one
explicit `--model`/`-m` argument. Adapters without that interface require a
reviewed integration or manual intake. Quota normalization currently parses
only the source implementation's Codex/Claude/Antigravity JSON shapes. Other
providers can supply the same normalized snapshot through a verified collector;
there is no live collector bundled for any provider.

## Start with local files

Install/update/remove this directory using the repository-level instructions.
For a manual install, copy the whole directory into the skill directory your
host documents. Make no edits to global instructions merely to run the helpers.
For an update, stage a new copy, compare changes and back up the old copy first;
do not overwrite user configuration. Remove only the copy you installed, after
stopping its writers. Runtime `.ai-relay` data and authorization files belong to
the project/operator and should be retained until separately reviewed.

Commands below run from this skill directory. Use `python3` if that is the local
Python command. `PROJECT`, notes and evidence files are paths chosen by the
operator; quote paths with spaces in the shell. JSON paths use normal escaped
Windows paths or `/` separators; no fixed home directory is assumed.

```text
python scripts/quota_guard.py evaluate --snapshot assets/quota.snapshot.example.json --unit assets/next-unit.example.json
```

This deliberately exits **2**, because the example quota/costs are unknown.
Never replace unknown fields with invented estimates. A passing gate requires
every applicable window, its actual observation/reset time, context evidence,
and a justified cost estimate/reserve in matching units. The default safety
factor is 1.5; it is configurable per unit and is not a vendor guarantee.
The existing context reminders apply at 50/70/90 percent when the effective
window is known and at most 500,000 tokens. These are conservative engineering
defaults, not provider limits or an automatic compaction trigger.

Create a note from `assets/HANDOFF.template.md` and a verification index as shown
in [protocol](references/protocol.md). Stop all writers before asserting quiescence.

```text
python scripts/handoff.py checkpoint --project PROJECT --note NOTE.md --files result.txt evidence/check.log --handoff-id source-001 --task-id task-001 --next-unit-id unit-002 --owner origin --quiescent --verification-file verification.json
python scripts/handoff.py verify --project PROJECT --live
```

Keep the returned source ID/hash and `origin_ref` in the origin's own progress
record. A successor fills `assets/RETURN_HANDOFF.template.md` and
`assets/return-receipt.example.json`, checkpoints outputs plus `return-receipt.json`,
then supplies the explicit return ID/hash. See [the full return recipe](references/return-handoff.md).

```text
python scripts/handoff.py inspect-return --project IMPORT_ROOT --handoff-id RETURN_ID --manifest-sha256 RETURN_HASH --origin-ref ORIGINAL_ORIGIN_REF --source-handoff-id ORIGINAL_SOURCE_ID --source-manifest-sha256 ORIGINAL_SOURCE_HASH
```

Both complete frozen checkpoint directories must be available in the isolated
import root. Exit 0 is integrity/readiness for review; a `blocked` or `failed`
unit can be intact. It never merges files or claims task completion.

## Optional native dispatch

Start from `assets/relay.config.example.json` outside the project. Every profile
is disabled. Fill exact executable, argv/model, project/task allowlists and
non-secret authorization/account/pool aliases only from existing authorization.
Verify subscription eligibility and Fast off through the native surface; record
`auth_verified`, `billing_verified_at`, and `fast_mode:false`. These are operator
attestations, not live checks. Inspect inherited CLI settings too.

```text
python scripts/dispatch.py plan --project PROJECT --config EXTERNAL_REGISTRY.json --target native-provider --snapshot SNAPSHOT.json --unit UNIT.json
```

Only `launch --execute` starts a child. Python/scripts used in tests are local
fake receivers. Do not configure an unknown executable. Native CLI subscription
allowance and vendor API billing are separate: this dispatcher rejects known
API environment variables, requires subscription mode, disables Fast by policy,
and never purchases quota or bypasses login/permission controls. It cannot
inspect arbitrary executable behavior or enforce truthfulness of attestations.

## Tests and limits

```text
python -B -m unittest discover -s scripts -p "test_*.py" -v
```

Tests use synthetic quota, local temporary files and bounded fake child processes.
Windows/Python 3.12 is the local release-validation target; macOS/Linux and real
native providers need separate execution evidence. Some symlink tests may skip
on Windows without symlink permission. Dispatch requires same-filesystem hard
links for atomic permits; failure blocks before spending its single attempt.
Its 60-second ACK ceiling may be too short for a real model. Timeout never kills
or retries the child and does not prove ownership is free. An operator must
reconcile processes, descendants and side effects before releasing a lock.

Files may still contain secrets despite path filters: review content before
sharing. Hashes detect changed bytes, not truthful tests or authorship. No
exactly-once remote effects, hostile-process defense, unattended monitor,
cross-host lock, automatic upload, integration ledger or automatic return to an
app is implemented. See [provenance](CHANGELOG.md) for source boundaries.
