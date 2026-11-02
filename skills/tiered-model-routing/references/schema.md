# Policy and catalog contract

This is the normative schema description; `scripts/route.py` validates it without
external dependencies. Unknown keys, duplicate JSON keys, booleans in integer fields,
non-finite numbers and malformed dates are rejected. Policy/catalog data are caller
declarations, not cryptographic proof. Inspect original native evidence before dispatch.
Never store credentials in either file. Keep account-specific files outside Git.

## Policy schema 1

| Field | Requirement |
| --- | --- |
| `schema_version` | Integer `1` |
| `max_evidence_age_hours` | Integer 1–24; maximum age for all native evidence |
| `providers` | Nonempty mapping of your provider keys to configurations |

Each provider has a nonempty `surface` token and `tiers` containing exactly D1–D4.
Optional `access_mode` is `subscription_cli` (default) or `host_native`. Host-native
mode additionally requires `host_control_authorized: true`, recording existing user
authorization to use that host's supported controls. This field is not accepted as
CLI authentication or subscription evidence.

Each tier has:

| Field | Requirement |
| --- | --- |
| `family` | Exact family token from inspected native evidence |
| `effort_preference` | Nonempty ordered list of distinct native effort tokens; the first supported token wins |
| `modes` | Mapping of native mode names to requested booleans; CLI routes must explicitly set `fast` |
| `min_context_tokens` | Positive integer or null when no minimum was requested |

The package does not rank effort labels or know their semantics. Put only user-permitted
effort alternatives in preference order. Requesting an unrecognized mode blocks.
For a host tool that exposes no mode controls, use an empty `modes` map; that means
no mode setting is requested or claimed. It does not mean inherited modes are off.

Optional `fallback` is accepted only on D4 of a subscription CLI policy. It contains
`family` (different from primary) and a nonempty `reasons` list drawn from:
`subscription_not_entitled`, `model_quota_exhausted`,
`subscription_requires_paid_credits`, `model_not_offered`. No fallback is configured
by default. Mode/effort/context requirements remain those of the original D4 policy.

## Catalog schema 2

| Field | Requirement |
| --- | --- |
| `schema_version` | Integer `2` |
| `provider`, `surface` | Exactly match the selected policy |
| `observed_at` | ISO UTC timestamp ending in Z or +00:00; at most 5 minutes into the future |
| `source_kind` | `native_catalog`, `native_probe`, `native_config`, `official_docs`, `fixture`, or `native_control_catalog` |
| `source_ref` | Nonempty reference to the inspected original evidence; never credentials |
| `account_alias` | Non-secret local alias, not an email/account number; use a host alias for host-native mode |
| `authenticated` | Boolean declaration; subscription CLI plans require true |
| `models` | List of model objects below; empty is valid input but cannot produce a ready plan |

Only authenticated `native_catalog` or `native_probe` evidence can support a CLI
plan. Configuration says what was requested, not account entitlement. Public docs
and fixtures never establish account access. Do not change labels to bypass this check.

Each model requires exactly:

| Field | Requirement |
| --- | --- |
| `id`, `family` | Nonempty tokens; exact IDs must be unique within the catalog |
| `version` | Nonempty list of nonnegative integers from the native model version, not client build |
| `available` | true, false or null for unknown |
| `efforts` | List of distinct native effort tokens |
| `modes` | Mapping from native mode names to supported boolean states, e.g. `{"fast": [false, true]}`; empty list means no confirmed state |
| `mode_billing` | Mapping for declared modes to `subscription`, `paid` or `unknown`; applies to enabling that mode |
| `context_tokens` | Positive integer or null for unknown; evidence must reflect the relevant native surface |
| `billing` | `subscription`, `api`, `usage_credits` or `unknown` |
| `subscription_eligible` | true, false or null for unknown |
| `quota_status` | `available`, `exhausted` or `unknown`; never infer from exit code or entitlement alone |

Version arrays compare numerically: `[1, 10]` is newer than `[1, 9]`; trailing zeros
are equivalent. Multiple latest IDs in a family are ambiguous and blocked. Provide
the applicable native model/variant evidence, not an arbitrary renamed family that
hides a newer model. A generic schema cannot verify vendor naming semantics: inspect
the referenced catalog to confirm the ID/family/version mapping and capability claims.

Every requested mode state must be explicitly supported, including false (disable).
An enabled mode additionally needs `mode_billing: subscription`. Paid/unknown Fast
or any other paid/unknown enabled mode is blocked, including on a host-native route.

## Explicit host-native control evidence

In addition to policy opt-in and existing user authorization, the catalog must use
`source_kind: native_control_catalog` and include `host_control` with exactly:

```json
{
  "name": "example.spawn_agent",
  "source_ref": "reference-to-inspected-native-control-schema",
  "observed_at": "2000-01-01T00:00:00Z",
  "supported": true
}
```

This snippet is synthetic and deliberately expired. Inspect the current host tool
metadata and record the real observation time; do not use the snippet as evidence.
The selected model must appear in that native catalog. `authenticated` may be false
because no separate CLI account verification is performed. Model `billing` must be
`unknown` and `subscription_eligible` null. `quota_status: unknown` is preserved;
`exhausted` blocks. Effective values remain null, even with `HOST_TOOL_READY`.

This is for already-authorized built-in host tools, not arbitrary scripts, APIs or
external CLIs relabeled as host tools. No quota purchase or login bypass is authorized.
The helper cannot invoke the control; the caller must use only the supported native
interface and recheck effective execution metadata after dispatch.

## Optional D4 subscription fallback evidence

Catalog `fallback_evidence` requires all fields below. The corresponding fallback
must already be enabled in policy. It never changes Council or host-native policy.

```json
{
  "scope": "general_d4",
  "from_family": "example-expert",
  "to_family": "example-strong",
  "primary_model_id": "exact-primary-native-id",
  "fallback_model_id": "exact-fallback-native-id",
  "reason": "model_quota_exhausted",
  "observed_at": "2000-01-01T00:00:00Z",
  "source_kind": "native_probe",
  "source_ref": "SYNTHETIC denial illustration, not evidence",
  "account_alias": "example-account",
  "authenticated": true
}
```

The example is deliberately expired. Both sources must be authenticated native
catalog/probe evidence, the alias must match, and the original primary/fallback IDs
must match the newest relevant catalog entries. Contradictory entitlement, availability
or quota evidence blocks. A missing primary requires explicit native unavailability
evidence, not a guess from its omission. No older fallback is selected if the newest
one lacks subscription eligibility, capacity or required modes.

## Output and exit codes

- `BLOCKED` / exit 2: invalid input or missing/conflicting required evidence.
- `PLAN_READY` / exit 0: offline subscription CLI plan only.
- `HOST_TOOL_READY` / exit 0: separate plan for explicitly authorized host controls;
  billing and unexposed quota remain unknown.

`requested`, `resolved` and `effective` use the same top-level setting keys. Requested
effort is a preference list; resolved effort is one value. Requested context is a
minimum, resolved context is the declared capacity. Effective values always remain
null because this package executes nothing. `live_verified` is always false and
`execution` is always `not_performed`. Neither ready status proves execution,
account access, a provider's advertised context nor a billing entitlement.

## 中文重點

policy schema 1 設定供應商、介面、D1–D4 家族與 effort／模式需求；catalog schema 2
記錄原生觀察。兩者均是需要人工或宿主核對來源的宣告，程式不驗證其真實性。
訂閱 CLI 需已登入、新鮮原生來源、訂閱資格、可用額度。未知時停止，不猜值。
內建工具需另行明確啟用 host_native 並具既有授權和原生控制證據，回傳不同狀態，
計費保持未知。備援只適用一般任務 D4，須綁定同帳號及精確模型 ID；不能因逾時、
拒絕、模式缺少或未知計費切換。所有範例均非實測證據。
