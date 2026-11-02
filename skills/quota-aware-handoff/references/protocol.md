# Handoff protocol and helper interfaces

## Contents

- Budget snapshot and next-unit estimate
- Checkpoint and verify
- Authorized CLI dispatch
- Receiver ACK and permit
- Recovery and limitations

## Budget snapshot and next-unit estimate

Use timezone-aware ISO timestamps. A nonsecret `account_ref` is a local alias, never an email/token/API key. `quota_pool_id` represents the actual shared entitlement, not a window or a UI name. Use stable limit IDs for every applicable short, weekly and model-specific window.

Helper CLI stdout is ASCII-safe JSON so it can be decoded consistently even on a legacy Windows console. Parsing the JSON restores Unicode names and paths; saved documents and JSON files remain UTF-8. The dispatcher accepts UTF-8 JSON with or without a BOM and pins the original bytes, including any BOM.

Illustrative snapshot below is **synthetic**, not the user's balance. Never reuse its timestamp as current evidence:

```json
{
  "schema_version": 1,
  "provider": "codex",
  "quota_pool_id": "openai-personal",
  "account_ref": "personal",
  "model": "VERIFIED_MODEL",
  "observed_at": "2026-10-29T04:00:00Z",
  "freshness_seconds": 120,
  "coverage_complete": true,
  "hard_stop": false,
  "limits": [{
    "id": "codex/primary", "unit": "percent", "remaining": 35,
    "observed_at": "2026-10-29T04:00:00Z",
    "source": "codex:account/rateLimits/read",
    "reset_at": null, "confidence": "measured"
  }],
  "context": {"effective_window": 200000, "remaining_tokens": 150000, "used_percent": 25}
}
```

```json
{
  "id": "unit-002",
  "quota_pool_id": "openai-personal",
  "model": "VERIFIED_MODEL",
  "estimate_basis": "Replace with actual comparable samples and conservative upper-bound method",
  "safety_factor": 1.5,
  "context_needed_tokens": 40000,
  "estimates": {
    "codex/primary": {"unit":"percent", "next_cost_upper_bound":8, "handoff_reserve":5, "uncertainty_margin":3}
  }
}
```

In this teaching example the requirement is `1.5×8+5+3=20` percentage points of the **same** window; 35 is enough for that one constraint. It says nothing about a missing weekly window. Real use must represent every applicable window and its estimate. Unit cost includes the receiver's intake and verification when dispatching a handoff. `context_needed_tokens` includes both the next unit and context reserve.

Use `assets/quota.snapshot.example.json` and `assets/next-unit.example.json` as fail-closed templates: unknown fields remain null. Do not change an unknown field simply to pass the gate.

```text
python scripts/quota_guard.py evaluate --snapshot quota.snapshot.json --unit next-unit.json
```

Exit 0 / `CONTINUE` admits the stated next unit. Exit 2 / `CHECKPOINT_AND_HANDOFF` blocks expansion. The `--now` option exists for deterministic tests, not for making stale production evidence pass. Estimate-basis text is recorded evidence, not automatically validated statistical calibration. Unknown quota or missing initial calibration means stop, save, and wait; calibration itself would require a separately authorized bounded pilot with its own budget.

Normalize captured official data without inference calls:

```text
python scripts/quota_guard.py normalize --provider claude --input official.json --output quota.snapshot.json --pool anthropic-personal --model ACTUAL_MODEL --account-ref personal --observed-at ACTUAL_ISO_TIME
```

`--coverage-complete` is a caller/adapter attestation after checking all applicable buckets. Default is false. Do not pass it mechanically. Antigravity amount-only buckets require verified `--amount-unit BUCKET=credits` or `requests` / `tokens` mappings; absence stays unknown. `--input -` accepts stdin. Only Codex/Claude/Antigravity official payload parsers are bundled; Grok subscription observations must be mapped by a verified capture source into this schema.

Every named Codex bucket must expose at least one recognizable window. A bucket with no primary or secondary balance is retained as a normalization issue, even if another bucket has ample quota and coverage was attested. A valid primary with an optional null secondary remains valid. Missing evidence is not evidence that a bucket is inapplicable.

## Checkpoint and verify

Save actual files first. Choose one `handoff_id` that is never overwritten. Complete the note and provide verification records as data:

```json
[
  {"argv":["python","-m","unittest"],"cwd":".","exit_code":0,"status":"passed","timestamp":"ACTUAL_TIME_WITH_TIMEZONE","evidence_files":["evidence/check.log"]}
]
```

Statuses are `passed`, `failed`, `not_run`, `skipped`. `passed`/`failed` require a bounded non-empty argv, a project-relative cwd, and a consistent exit code. `not_run`/`skipped` require `exit_code: null`. They may carry `argv: null` or `[]` and `cwd: null` when no command was run, or a known bounded command with a project-relative cwd. Do not claim a test passed if it was not run. Every evidence file must be explicitly included among checkpoint artifacts. The helper never executes these argv records.

The manifest `verifications` array is a compact, project-relative **index** and keeps only `argv`, `cwd`, `exit_code`, `status`, `timestamp` and `evidence_files`. Keep the full exact observation in a hashed artifact listed in `--files` and referenced from `evidence_files`. That record includes an absolute or out-of-project cwd (for example `verify --project PROJECT` run from the parent), end times and scope notes. Omit an index entry that cannot be expressed truthfully. Never alter the actual cwd or invent placeholder argv to satisfy the schema. Helper timestamps are canonical UTC ISO 8601. Human-readable notes may also include the operator's local timezone.

```text
python scripts/handoff.py checkpoint --project PROJECT --note FILLED_HANDOFF.md --files src/app.py evidence/check.log output/report.pdf --handoff-id handoff-001 --task-id task-001 --next-unit-id unit-002 --owner codex --quiescent --verification-file verification.json
python scripts/handoff.py verify --project PROJECT --live
```

All artifact paths are project-relative regular files. The helper rejects traversal, symlinks, reserved control files and common secret filenames. This is not content-based data-loss prevention: review files and logs for embedded secrets before sharing. Record deletions in the note; deleted paths are not copyable artifacts. If no implementation output exists yet, a truthful saved progress/status document can be the artifact.

Canonical layout:

```text
.ai-relay/current.json
.ai-relay/checkpoints/<handoff-id>/HANDOFF.md
.ai-relay/checkpoints/<handoff-id>/manifest.json
.ai-relay/checkpoints/<handoff-id>/manifest.sha256
.ai-relay/checkpoints/<handoff-id>/artifacts/<relative-path>
HANDOFF.md  (human-readable convenience copy)
```

The checkpoint lock serializes checkpoint writers. A temporary snapshot is published by rename, then the pointer is replaced atomically. Old IDs are retained. Verify confirms the pointer/manifest/note/artifact hashes and optionally compares current live files. `--handoff-id ID` checks that specific immutable snapshot instead of following current. Optional `--max-age-seconds` enforces a chosen checkpoint age; there is no arbitrary expiry of useful work by default.

The fully serialized manifest must fit the verifier's 8 MiB JSON limit before publication. A compact input within its limit can still expand past that boundary; in that case checkpoint creation fails while the previous canonical pointer and note remain intact.

Exit 0 indicates checkpoint creation or integrity verification; exit 1 indicates failure. `verified:true` does not mean the product works or all recorded tests passed. Review warnings and actual logs before acknowledgement.

With `--live`, `convenience_note_matches` reports true/false for a readable project-root `HANDOFF.md`, or null when absent/unreadable/not checked. A mismatch or read error produces a warning while the valid canonical snapshot remains authoritative. Receivers must use the exact canonical `handoff_path` and `manifest_path`, not the convenience copy. `quiescent_asserted_at_checkpoint:true` describes the prior owner's assertion; `runtime_writers_verified:false` explicitly says that no process proof was obtained. Legacy `quiescent:true` is an assertion alias, not a live one-writer test.

## Authorized CLI dispatch

Start with `assets/relay.config.example.json`, store the completed registry **outside the project**, and fill it from existing user authorization and observed local configuration. Keep profiles disabled until evidence is complete. Do not put credentials into the registry.

Each enabled profile requires a safe provider identifier, verified Fast off (`fast_mode:false`), exact account alias, pool, explicit model in argv, absolute executable, subscription billing mode and recent verification, authorized project roots, exact task IDs, and an authorization reference. Wildcards are not a substitute for scope. API/on-demand execution is not implemented by this dispatcher; it requires a separately designed and explicitly budgeted adapter.

```text
python scripts/dispatch.py plan --project PROJECT --config EXTERNAL_REGISTRY.json --target claude --snapshot CLAUDE_SNAPSHOT.json --unit CLAUDE_NEXT_UNIT.json
python scripts/dispatch.py launch --execute --project PROJECT --config EXTERNAL_REGISTRY.json --target claude --snapshot CLAUDE_SNAPSHOT.json --unit CLAUDE_NEXT_UNIT.json
```

`plan`, and `launch` without `--execute`, inspect without creating a child. The chosen provider is explicit; the Skill selects a candidate after evaluating readiness. The target's unit ID must equal the canonical `next_unit_id`. Account, model and pool must match the registry and snapshot. `billing_verified_at` defaults to a 24-hour validity limit; verify provider-specific actual auth and fallback settings, not just whether an environment variable exists.

Launch acquires an exclusive dispatch lock, re-verifies live files and quota, reserves one attempt for the whole handoff ID, then starts a process with `shell=False`. Known API billing environment variables, permission-bypass flags, indirect shell/eval launch, stale billing confirmation, and unauthorized scope are rejected. Existing CLI settings may contain additional behavior the script cannot inspect; the profile is a truthful operator attestation, not a security proof.

Before reserving that attempt or starting a child, launch checks that the observations, context evidence, reset boundaries and billing attestation cover the full ACK deadline. It also probes atomic hard-link publication on the actual relay filesystem using its own temporary files; an unsupported filesystem blocks without spending the attempt. Plan remains read-only. An active checkpoint lock blocks preflight. These checks reduce avoidable failed launches; they do not establish that all unrelated processes have stopped.

Authorization and unit definitions are pinned for the attempt using hashes of the exact bytes parsed and validated, rather than a later reread of the files. A mid-check replacement must block the launch or permit. The quota snapshot may receive legitimate newer observations while the receiver verifies the handoff: re-read its identity, completeness, age and budget, and require observation time not to regress. Preserve bucket identities and units, and do not widen the freshness duration. A fresh lower balance is accepted only if the full gate still passes. An increase requires evidence of a passed prior reset and a valid new window; otherwise reject unobserved budget growth. The receiving agent must not author its own quota evidence. These are consistency checks on a trusted collector's data, not cryptographic proof of provider usage. Check that the receiver is still running immediately before publishing its permit; process liveness and file publication cannot be made fully atomic by this cooperative protocol.

## Receiver ACK and permit

The launcher supplies exact paths and identity in its receiving prompt. Before any substantive work, the receiver verifies the canonical files and existing evidence, checks scope and quota, and atomically writes:

```json
{
  "handoff_id": "handoff-001",
  "task_id": "task-001",
  "next_unit_id": "unit-002",
  "target": "claude",
  "manifest_sha256": "EXACT_CANONICAL_HASH",
  "status": "verified_ready",
  "checks": {"artifacts":true,"validation_evidence":true,"authorization_scope":true},
  "timestamp": "ACTUAL_CURRENT_ISO_TIME"
}
```

ACK path: `.ai-relay/acks/<handoff-id>.json`. The dispatcher rejects stale, reused, mismatched or persistently incomplete acknowledgements and rechecks the checkpoint/budget before publishing `.ai-relay/permits/<handoff-id>.json`. The receiver must validate the permit against its handoff, task, unit, target and manifest digest before proceeding. Until then, only intake checks and the ACK write are allowed. Report `ACKNOWLEDGED` only after this handshake; it does not mean the next unit or the whole task completed.

Continue to publish ACKs atomically. As a defensive measure, the dispatcher waits for a temporarily unreadable or incomplete ACK until the existing deadline; once a complete JSON object is available, an invalid identity, status, check or timestamp is terminal. This never extends the deadline or admits a late ACK. Permit publication stays atomic and does not fall back to a partially written public file. Post-ACK quota checks use current wall-clock time and include the gate's actual reasons in failure reports.

**Manual operator intake.** When the user/operator starts the receiver directly instead of `dispatch.py`, no ACK or permit file is exchanged. Do not write a substitute permit or report `ACKNOWLEDGED`. Record in the note: `start_type: manual_operator_intake`, the actual workspace and owner, the explicit authorization reference, and the limited writer evidence (operator single-writer assignment plus `quiescent_asserted_at_checkpoint`, with `runtime_writers_verified:false` unless real process proof exists). This replaces only the launch handshake. Canonical verification, protected states and `quota_guard.py evaluate` still apply. If quota is unknown, finish intake and checkpoint `WAITING_FOR_QUOTA`. Dispatcher launches always keep the full ACK/permit protocol above.

The bundled ACK timeout is capped at 60 seconds. Local fixture timing does not establish that a real headless model can read the Skill, verify the package and obtain its tool permissions within that time. Measure the intended CLI's actual handshake before relying on unattended dispatch; use the documented interactive receiving path when that capability is unverified.

## Recovery and limitations

The reference launcher deliberately leaves its cooperative ownership lock in place after acknowledgement or ambiguous failure. It is a **single-transfer helper**, not a lifecycle supervisor. Before the next transfer, an operator or a separately verified supervisor must check that the old process and all descendants stopped, reconcile any external side effects, save/verify the current checkpoint, and release only the matching dispatch lock. Keep the previous attempt record. Do not auto-delete locks on a timer or use a new ID to retry unknown side effects.

If the child exits before ACK or the ACK deadline expires, retain the process/attempt evidence and report `RECOVERY_REQUIRED` / `WAITING_FOR_ACK`. Do not assume a timeout killed the child, force-kill unrelated processes, or start a second writer. A configured child timeout is not a guarantee that all descendants stopped.

**Headless `WAITING_FOR_QUOTA` recovery.** A headless receiver that observed no quota keeps the checkpoint at `WAITING_FOR_QUOTA` and does not author its own snapshot. To resume:

1. Obtain an official payload from an **existing**, already authorized source on the same account and pool. One source is an interactive session whose statusline captured Claude `rate_limits`. The other is a trusted supervisor. Keep its original observation timestamp. Do not add a new collector, read credentials, change login/billing, use API fallback, or treat `cost.total_cost_usd`/API spend as subscription quota.
2. Confirm that every applicable window, the context and the billing mode are present. Write a bounded next-unit estimate for exactly the pending unit, in the same units.
3. Run `quota_guard.py normalize --provider claude --input PAYLOAD.json --output SNAPSHOT.json --pool POOL --model ACTUAL_MODEL --account-ref ALIAS --observed-at ORIGINAL_ISO_TIME`. Add `--coverage-complete` only after actually checking all windows.
4. Run `quota_guard.py evaluate --snapshot SNAPSHOT.json --unit UNIT.json` at the current time (no `--now`). Resume substantive work only on exit 0 / `CONTINUE`. Otherwise stay stopped and record the reasons.

A headless CLI may provide no captured subscription quota. Verify the actual surface; missing evidence must remain unknown.

SHA-256 detects corruption relative to a saved digest; it cannot authenticate an attacker who rewrites all files and digests. The filesystem locks coordinate cooperating local participants; they do not enforce permissions against arbitrary agents or synchronize multiple hosts. Keep shared-cloud execution behind an actual coordinator. This package does not claim exactly-once external side effects, a running daemon, or a verified end-to-end production relay.
