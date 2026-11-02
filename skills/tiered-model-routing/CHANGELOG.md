# 2.0.0-rc.1

- Generalize the private 1.0.1 deterministic planner into explicit provider, surface,
  tier, family, effort and mode policy. No personal account data or execution history.
- Keep score boundaries, risk floors, failure escalation, numeric version ordering,
  freshness, subscription restrictions and separate requested/resolved/effective fields.
- Require explicit quota state and account-bound evidence for optional D4 fallback.
- Default Fast off; require evidence that every enabled mode is subscription-included.
- Add explicit host-native planning with `HOST_TOOL_READY`, existing authorization and
  supported native control evidence. Unexposed billing/quota stay unknown.
- Catalog schema 2 and policy schema 1 replace the private schema and hardcoded parsers.
- Include offline tests and bilingual usage docs. No native inference is performed.
