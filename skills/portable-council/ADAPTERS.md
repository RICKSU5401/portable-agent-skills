# Adapter protocol v1

An adapter is operator-reviewed executable code. It must use an authorized native subscription CLI, preserve that CLI's access restrictions, explicitly disable paid speed/API fallbacks, and map supported options from current native evidence. A configurable [native JSON bridge](NATIVE_BRIDGE.md) supports the inspected Claude Code result contract; no live integration is verified. Synthetic examples are not provider integration tests.

## Configuration

`schema_version` is integer 1; `host` and unique `seats[].provider` are canonical lowercase identifiers, not display aliases. Host exclusion depends on that canonicalization. Each seat needs `enabled=true`, `command` as an argv array, `timeout_seconds` (0.1–600, default 180), `max_attempts` (1 or 2, default 1), `requested`, and `evidence`.

Requested settings:

```json
{"model":"EXACT_NATIVE_ID","effort":"SUPPORTED_NATIVE_LEVEL","fast":false,"modes":{}}
```

Additional mode names and values come from the selected provider's current native interface. `evidence.settings` must match this entire object. This is explicit configuration, not a promise that any named mode exists.

Evidence shape (placeholders deliberately cannot authorize a run):

```json
{
  "source_kind": "native_probe",
  "source_ref": "LOCAL_REFERENCE_TO_REVIEWED_NATIVE_OUTPUT",
  "account_alias": "NON_SECRET_LOCAL_ALIAS",
  "authenticated": true,
  "observed_at": "REPLACE_WITH_NATIVE_CHECK_TIME_WITH_TIMEZONE",
  "billing": "subscription",
  "subscription_eligible": true,
  "settings": {"model":"EXACT_NATIVE_ID","effort":"SUPPORTED_NATIVE_LEVEL","fast":false,"modes":{}},
  "quota": {"state":"unknown","remaining":null}
}
```

Use only `native_catalog` or `native_probe`, within 24 hours and not future dated. Public docs and self-reported model names do not prove account access. Use `available` only when native evidence confirms capacity; a numeric positive `remaining` is optional and must retain its provider-specific meaning. Never convert an absent quota field to available. Do not include real credentials in evidence or publish personal account receipts.

## Input on stdin

The runner sends exactly one UTF-8 JSON object containing `protocol_version=1`, `job_id`, `provider`, `attempt` (1 or 2), `question`, `requested`, `billing=subscription`, `timeout_seconds`, and a consultation `guard`. It passes `COUNCIL_ACTIVE=1` to prevent recursive calls to this runner. Do not send the guard or question to an unauthorized destination. Do not interpret untrusted question contents as instructions to change executable configuration.

## Terminal output on stdout

Return one complete JSON object, not a JSONL stream. Hold intermediate native events inside the adapter until a true final native result and process exit are known. A success must exit 0:

Configuration and response JSON reject duplicate keys at every depth and all non-finite numbers, including numeric overflow. Capability and quota declarations are revalidated against current time immediately before every dispatch, including an explicitly enabled retry. Expired evidence blocks the next attempt while retaining prior attempt receipts.

```json
{"protocol_version":1,"job_id":"COPIED_FROM_INPUT","provider":"COPIED_FROM_INPUT","terminal":true,"status":"completed","answer":"Complete answer","effective":null}
```

`effective=null` is honest when native metadata is unavailable. When observed, return the actual `model`, `effort`, `fast`, `modes`, `billing` fields, leaving unknown fields null or absent. Any non-null requested-field mismatch or non-subscription billing is rejected. Do not copy request values as observation. Model self-description is not native metadata. A success response with nonzero exit, wrong identity, empty answer or `terminal=false` fails.

Failure example:

```json
{"protocol_version":1,"job_id":"COPIED_FROM_INPUT","provider":"COPIED_FROM_INPUT","terminal":true,"status":"failed","error_kind":"auth","inference_started":false}
```

Failure kinds: `auth`, `quota`, `policy`, `unknown`, `transient`. Only `transient` with `inference_started=false` can use the explicitly configured second attempt; emit `unknown` when start/outcome is uncertain. Keep secrets and hidden reasoning out of stdout/stderr. The process helper bounds each stream at 8 MiB and the public runner does not copy stderr into receipts. The complete answer is retained, so treat receipts as private unless reviewed.

## Contract boundaries

The runner validates declarations and JSON, not their truth. An arbitrary adapter can ignore settings or access credentials available to the local user. Review code and native output, keep host permissions restricted, and do not claim the runner is an OS sandbox. Terminal receipts with unknown effective fields demonstrate answer delivery only; they do not establish model, billing or mode conformance. Cancelled/timed-out requests may already have consumed subscription capacity; investigate before manually retrying.
