# Return results to the originating conversation

## Contents

- Establish the return contract
- Prepare the successor's return
- Inspect and reconcile at the origin
- Receipt fields and limits
- Failure and delivery boundaries

## Establish the return contract

Before delegating, assign one stable non-secret `origin_ref` and record the real originating conversation, return destination, task and bounded unit in `HANDOFF.md`. Use safe ASCII identifiers (1–128 letters, digits, dot, underscore or hyphen, starting with a letter/digit); keep the human conversation title separate. Follow the existing authorization, model-routing and quota gates.

Create and verify the source checkpoint. Retain its exact ID and manifest SHA-256 in the origin's own outstanding delegation record or original conversation, together with `origin_ref`. Pass those values to the successor in the receiving instruction. The source manifest's `next_unit_id` identifies the delegated unit. Do not make a note contain its own manifest hash; retain the hash after checkpoint creation. Do not resolve the original source later from `.ai-relay/current.json`, which advances with new checkpoints.

Keep a return reserve and rolling partial results while executing. If an older handoff lacks a return contract, recover the source pins and destination from the operator's existing assignment before substantive work. When that evidence is unavailable, save a clearly unbound partial report; never invent origin identity or claim `inspect-return` passed.

## Prepare the successor's return

Return after the assigned unit finishes or stops, including blocked intake, partial progress, failure, quota stop or cancellation. Preserve the user's latest stop instruction; make only the permitted minimal save/status actions after stopping. Do not continue the task just to improve the completion label.

1. Stop substantive work and confirm writer/process status. Save actual outputs, errors, validation logs and unresolved effects. If no implementation output exists, save a truthful status document.
2. Fill `assets/RETURN_HANDOFF.template.md`. Name the working note `RETURN_HANDOFF.md`. Keep original task requirements/protected conditions, the unit outcome, whole-task remaining work, changed paths, actual model/settings and source pins visible.
3. Save **project-root `return-receipt.json`** from `assets/return-receipt.example.json`, using actual values. Give the return a new immutable ID. Do not add its own manifest hash to the receipt or note. Record deletions as proposals in the note; never encode an automatic deletion command.
4. Create a normal checkpoint with that note, the receipt, actual output files and evidence. Use the original `task_id`; a return checkpoint's `next_unit_id` can be `origin-reconcile`. Prepare a truthful verification index using `protocol.md`; no unrun check becomes passed.

```text
python scripts/handoff.py checkpoint --project PROJECT --note PROJECT/RETURN_HANDOFF.md --files return-receipt.json output/result.json evidence/check.log --handoff-id RETURN_ID --task-id ORIGINAL_TASK_ID --next-unit-id origin-reconcile --owner ACTUAL_SUCCESSOR --quiescent --verification-file PROJECT/verification.json
python scripts/handoff.py verify --project PROJECT --handoff-id RETURN_ID --live
```

Paths after `--files` are project-relative; give the other paths their actual absolute values when running elsewhere. Only pass `--quiescent` after the required writer check. The immutable note is saved as `.ai-relay/checkpoints/RETURN_ID/HANDOFF.md`; its content is the return report. Root `HANDOFF.md` remains only a convenience copy.

5. Supply the exact return ID/hash printed by checkpoint creation, actual files and a filled `assets/origin-receive-prompt.md`. Keep the source checkpoint available for comparison. Mark the package `RETURN_READY`; record delivery as `prepared` until the intended interface confirms delivery. If verifying fails, preserve the last valid checkpoint and report the failure, not `RETURN_READY`.

## Inspect and reconcile at the origin

The origin first retrieves **its own** expected `origin_ref`, source ID and source manifest hash from the outstanding delegation. The incoming message explicitly selects the return ID/hash. Supplying the receipt's self-claimed values as expectations proves only internal consistency and defeats wrong-origin/stale-baseline detection.

For a separate workspace, copy both complete immutable checkpoint directories into an isolated import root under `.ai-relay/checkpoints/`, keeping the exact bytes and relative paths. Inspect before merging into the working project. Never treat another host's absolute path as delivered content, extract untrusted traversal paths, or replace a different existing checkpoint with the same ID. The origin's normal current pointer need not be changed.

```text
python scripts/handoff.py inspect-return --project IMPORT_ROOT --handoff-id RETURN_ID --manifest-sha256 RETURN_SHA256 --origin-ref EXPECTED_ORIGIN --source-handoff-id EXPECTED_SOURCE_ID --source-manifest-sha256 EXPECTED_SOURCE_SHA256
```

The inspector verifies both frozen snapshots, rechecks the exact parsed manifest/receipt bytes against pinned hashes, requires the frozen `artifacts/return-receipt.json` in the return manifest, and binds origin/source/task/delegated unit/return identity. It deliberately does not compare source artifacts against live files: delegated work should change them. Exit 0 and `ready_for_reconciliation:true` allow the **origin's review**, not automatic acceptance. Failed, partial and blocked returns can pass integrity inspection while their outcomes remain unchanged.

After inspection:

1. Read actual returned artifacts and verification logs. Compare source, returned and current origin versions, current user instructions, acceptance criteria, source versions and protected task conditions.
2. Check whether the same return ID/hash is already recorded in the existing progress document. Re-inspection is safe; do not count or apply its work again. Treat the same ID with a different hash as a conflict. No automatic integration ledger is provided.
3. Reconcile writer/lock ownership and uncertain external operations before any writes or retries. Resolve conflicting edits explicitly; do not overwrite origin changes or enact proposed deletions just because the receipt exists. A return grants no new launch, permission, spending or publication authorization.
4. Run the smallest affected meaningful checks when evidence requires it. Record which results were inspected versus re-executed. Update the existing progress record with accepted return ID/hash, evidence, completed work, open work and unresolved conflicts. Mark `RETURN_INTEGRATED` only after this step. Evaluate the full user's acceptance criteria separately before `DONE`.

`dispatch.py` remains a single-transfer launcher. Its ACK and permit do not report completion, its exit does not emit a return, and inspection never releases its ownership lock. Follow `protocol.md` for operator reconciliation. Returning files does not automatically wake or launch the originating AI.

## Receipt fields and limits

| Required field | Meaning / validation |
| --- | --- |
| `schema_version`, `kind` | Integer `1` (not boolean), literal `return`. |
| `origin_ref` | Must equal the origin's independently retained safe identifier. |
| `source_handoff_id`, `source_manifest_sha256` | Must match the caller's expected source ID and exact lowercase 64-character SHA-256. |
| `task_id` | Must match both source and return manifests. |
| `delegated_unit_id` | Must match the source manifest's `next_unit_id`. |
| `return_handoff_id` | Must match the explicitly selected return checkpoint. |
| `outcome` | `completed`, `partial`, `blocked`, `failed`, or `cancelled`; describes only the delegated unit. |
| `summary` | Nonempty text, at most 16,384 characters. |
| `remaining_work` | Array of at most 1,000 nonempty strings, each at most 4,096 characters. Use `[]` only when no remaining item is known; this is not a whole-task completion verdict. |

JSON must be UTF-8 within the existing 8 MiB limit; duplicate keys and non-finite numbers are rejected. Additional fields are data only: a claimed `delivered`, `integrated` or `task_complete` field cannot grant acceptance. Put detailed delivery evidence, file changes, proposed deletions, routing and timestamps in the human report and hashed evidence; the current inspector does not validate those extra fields.

## Failure and delivery boundaries

Keep `prepared`, `delivered`, `RETURN_VERIFIED` and `RETURN_INTEGRATED` distinct. Only the origin can establish integration. The current helper does not inject a desktop conversation, upload to another provider, merge files, execute embedded commands or supervise process exit. Use an already authorized supported interface; otherwise provide a manual import package and say delivery is pending.

A hard kill or exhaustion may prevent a final report. Preserve the last valid checkpoint and identify missing or unknown work; do not fabricate a fresh return. Hashes establish identity relative to retained evidence, not authorship, truthful tests, up-to-date instructions or current single-writer ownership. Recheck those conditions at reconciliation, and pass the quota gate before starting further work.
