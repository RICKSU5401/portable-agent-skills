# RETURN_HANDOFF — 回傳原對話整合

## Identity and destination

- origin_ref: <original non-secret safe identifier>
- originating conversation / destination: <original title and authorized delivery location>
- source_handoff_id / source_manifest_sha256: <exact source pins received at delegation>
- task_id / delegated_unit_id: <source task and source next_unit_id>
- return_handoff_id: <new unique immutable identifier>
- reported_at: <actual ISO 8601 time with timezone>
- successor / workspace: <provider, surface, non-secret account alias and actual paths>
- outcome: <completed | partial | blocked | failed | cancelled; delegated unit only>
- delivery_status: <prepared | delivered, with actual evidence; origin owns integration status>

## Goal, latest instructions and protected conditions

<State the delegated scope and its acceptance criteria; preserve null/blocked/disabled conditions, source versions and original constraints. List later instructions received by the successor for the origin to reconcile.>

## Actual results and file changes

| Relative artifact path | Added / modified / proposed deletion | Saved result | Verification evidence |
| --- | --- | --- | --- |
| <path> | <change type> | <concrete result> | <log path and check> |

<Include the actual non-secret files in the checkpoint. List deletions as proposals; nothing in this report authorizes automatic deletion. If no substantive output exists, save a truthful progress report. Put the return manifest SHA-256 in the delivery message after checkpoint creation, not inside this hashed note.>

## Verification and limitations

<Exact commands, actual cwd, timestamps, runtime versions, exit codes, log paths and checked artifact versions. Separate passed, failed, skipped and not-run checks. State whether results came from fixtures, replay, independent review or a live external model. A model's assertion is not test evidence.>

## Outstanding work and effects

<Remaining whole-task work, failed attempts, unresolved conflicts, missing inputs, uncertain remote writes/jobs with operation IDs, and the smallest next action. Do not relaunch an operation with unknown status.>

## Writer, quota and routing evidence

<Successor writers/processes/agents stopped or still active, how checked, applicable ownership lock and operator reconciliation needed. Record quota source/age, gate/stop reasons and actual model/settings using model-routing.md. Keep requested, configured and observed values separate.>

## Origin reconciliation instruction

<Attach the filled origin-receive-prompt.md and explicit return ID/hash. The origin must obtain its own expected origin/source pins, inspect the frozen receipt, compare source/returned/live files, reconcile current instructions and meaningful checks, then update its existing progress record with the accepted return ID/hash. Until that happens, do not claim the original conversation has imported or integrated this work.>
