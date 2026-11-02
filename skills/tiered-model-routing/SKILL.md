---
name: tiered-model-routing
license: MIT
description: Grade task difficulty and prepare an evidence-based model plan using explicit provider, family, effort and mode policy. Keep requested, resolved and effective settings separate. Never launch models, buy capacity, bypass controls or claim unobserved billing or quota.
---

# Tiered model routing

Version: 2.0.0-rc.1. Read [README](README.md) or [繁體中文](README.zh-TW.md).

A skill supplies instructions; it cannot change the current conversation's model.
The helper prepares a plan only. Use a supported new-session or subagent control
after checking its native capability evidence and the user's existing authority.

## Grade the unit

Score each factor 0–3 and record an observable reason in the task record:

| Factor | 0 | 1 | 2 | 3 |
| --- | --- | --- | --- | --- |
| Complexity | Mechanical | One known component | Cross-component reasoning | Architecture or cross-system work |
| Uncertainty | Complete evidence | Small checkable gap | Conflicting or incomplete evidence | Major unknowns requiring experiments |
| Impact | Disposable | Local reversible change | Shared data or production configuration | Permissions, money, irreversible data or public release |

Totals 0–2 → D1; 3–4 → D2; 5–6 → D3; 7–9 → D4. Security, permissions,
release control and data migration set a D3 floor. Cross-provider work, unknown
mode semantics and conflicting mode semantics independently set a D4 floor.
After two failed repair attempts, increase one tier, capped at D4, and reassess.
Escalation does not authorize broader permissions, more spending or unlimited retries.

## Resolve using evidence

1. Read the user-configured provider/surface and D1–D4 family policy. Do not infer
   equivalent families, effort rankings or a vendor's latest model from this package.
2. Inspect fresh native evidence, including exact model IDs, numeric versions,
   effort support, mode controls, context, eligibility and quota. Keep a non-secret
   alias and evidence reference; never copy credentials. Evidence expires after at
   most 24 hours. Public documentation alone does not establish account access.
3. Select the newest numeric version in the requested family, then validate all
   requirements. Do not use an older version to obtain a missing mode. Equal latest
   versions with different IDs are ambiguous and stop the plan.
4. Run `scripts/route.py` with `--policy` and `--catalog`; see [schema](references/schema.md).
   Both files are untrusted declarations requiring inspection, not signed live evidence.
5. For a native subscription CLI, only `PLAN_READY` permits consideration of a
   dispatch. Unknown billing, eligibility or quota blocks. API accounts, usage
   credits and paid enabled modes are not accepted by this release.
6. Built-in host agent controls can use an explicit `host_native` policy with existing
   user authorization and fresh supported control/catalog evidence. That separate
   path returns `HOST_TOOL_READY`; unexposed billing and quota stay unknown. It must
   not be used as an API/CLI billing fallback or to buy capacity. Missing policy or
   control evidence blocks. The host's own authorization and controls still apply.
7. Keep `requested` (policy), `resolved` (plan), and `effective` (actual native
   execution) distinct. This helper always leaves effective fields null and
   `live_verified=false`. A successful exit code or model self-description is not
   an execution receipt. Inspect native execution for unexpected changes.

Fast defaults off in the subscription example. Any enabled mode needs explicit
policy, capability evidence and confirmed subscription inclusion. A native
subscription CLI and a separately billed API are different routes. Never buy
credits, raise limits, change authentication or bypass organizational restrictions.

General D4 fallback is optional, disabled by default and separately configured.
It requires fresh account-bound native evidence of a permitted subscription
unavailability reason. Timeout, networking errors, safety refusal, missing mode
support or unknown billing do not justify fallback. Preserve D4 and the original
requested family. Fixed council policy is independent; host-native mode has no
subscription fallback. See the schema for the explicit evidence contract.

## Report and hand off

Report difficulty and reasons, exact resolved ID, requested/resolved/effective
settings, evidence age, billing and quota status, and concrete blocked prerequisites.
If capacity is unknown or low, preserve the handoff budget and use the adjacent
quota-aware-handoff skill; do not spend the remaining allowance retrying blindly.
One artifact has one writer. A review agreement is not factual verification.

This package has only offline tests. It does not discover accounts, execute native
CLIs, expose live quota, schedule background upgrades or prove a provider's runtime
behavior. No private execution history or personal model defaults are included.
