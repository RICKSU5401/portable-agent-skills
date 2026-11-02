---
name: quota-aware-handoff
description: Preserve verified work before quota or context exhaustion, transfer one authorized unit, and return results to the origin for reconciliation. Includes conservative budget checks, immutable checkpoints, an optional cooperative CLI handshake, and read-only return inspection.
---

# Quota-aware handoff

Public candidate: 0.1.0. Read [README](README.md) or [繁體中文](README.zh-TW.md).
This instruction package and its Python helpers do not monitor providers in the
background, increase limits, grant permissions, or switch the current model.

1. Preserve the user's objective, latest corrections, existing authorization,
   output scope and acceptance criteria. Record the current owner, next bounded
   unit, stable task ID, and origin/return destination. Never convert unknown,
   blocked, disabled or untested states into success because files exist.
2. Determine the actual surface, model, subscription eligibility and shared
   quota pool using authorized native evidence. Keep requested, configured and
   observed settings distinct. Unknown quota stays unknown. Model selection is
   an independent prerequisite; see [model-routing](references/model-routing.md).
3. Before new work, evaluate every applicable limit using `quota_guard.py`:
   `remaining >= safety_factor * next_cost_upper_bound + handoff_reserve + uncertainty_margin`.
   Include tests, repair, concurrent consumers and the successor's return report
   in the estimate. Keep context and quota units separate. Unknown/stale data,
   incomplete windows, a passed reset without fresh evidence, or insufficient
   reserve means `CHECKPOINT_AND_HANDOFF`, not paid fallback.
4. Save rolling checkpoints while capacity remains. At a stop, drain writers,
   preserve useful partial artifacts, fill the handoff template, checkpoint only
   explicit non-secret files, and verify the frozen snapshot and live files.
   A quiescent flag is an assertion, not process proof. Keep one writing owner.
5. Choose only a successor already authorized for this project/unit, with its
   own fresh passing gate, eligible subscription, verified Fast off and ordinary
   tool permissions. CLI subscription access does not authorize a separately
   billed model API. Do not enable credits, overage, paid Fast, new accounts or
   permission bypass. Do not launch any provider merely because this Skill loads.
6. Optional `dispatch.py plan` is read-only. `launch --execute` requires an
   explicit external registry; it permits one attempt per handoff ID across all
   targets. Receiver verifies artifacts/scope/quota, writes an atomic ACK, waits
   for the matching permit, then performs only the assigned unit. A timeout does
   not prove the child stopped. Retain locks/attempts until operator reconciliation.
   Use manual intake when the real CLI handshake is not verified; operator
   assignment replaces only the handshake, never the quota/integrity checks.
7. Retain source checkpoint ID/hash and `origin_ref` independently at the origin.
   On success, failure, cancellation or partial stop, the successor saves actual
   results and a return receipt in a new checkpoint. No new provider launch is
   needed. Return the explicit ID/hash and files through an authorized interface.
   Saved files are `prepared`, not proof of delivery.
8. At the origin, run `handoff.py inspect-return` with the original retained pins.
   Success means `ready_for_reconciliation`. Compare source, returned and current
   files; reconcile instructions/conflicts/ownership; inspect real verification
   logs and rerun affected checks. No automatic merge, deletion, lock release or
   completion occurs. Record accepted return pins to prevent double-counting.

Report actual saved artifacts, checks, current owner, remaining work and the
smallest missing prerequisite. Distinguish fixture tests from native execution.
If exhausted, preserve the last valid checkpoint; do not invent a final report.

## References

- [Protocol](references/protocol.md): schemas, commands, ACK/permit and recovery.
- [Return handoff](references/return-handoff.md): frozen return and origin reconciliation.
- [Model routing](references/model-routing.md): configurable model/capability prerequisite.
- [Handoff template](assets/HANDOFF.template.md), [return template](assets/RETURN_HANDOFF.template.md).

The helpers are cooperative local tools, not a security boundary or distributed
coordinator. The shipped normalizers are parsers for documented shapes in the
source implementation, not live collectors or a promise about current providers.
