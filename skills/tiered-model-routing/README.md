# Tiered model routing

[繁體中文](README.zh-TW.md) · Version **2.0.0-rc.1**

A Python standard-library planner for task difficulty, native model capabilities
and subscription boundaries. It never sends a prompt or switches a model.
Provider names, families, exact model IDs, effort order and mode requirements come
from your policy and current native evidence. There is no built-in “latest” list.

## Try the safe example

Requires Python 3.10 or newer. From this skill directory:

```text
python -B scripts/route.py --provider example-provider --complexity 2 --uncertainty 2 --impact 1 --policy examples/policy.json --catalog examples/catalog.fixture.json
python -B -m unittest discover -s scripts -p "test_*.py" -v
```

The example must return **BLOCKED** with exit code 2: its timestamp is deliberately
expired, identity is synthetic, account is unauthenticated and billing/quota are
unknown. Do not merely relabel it as native evidence. Create a separate local catalog
after inspecting your actual account and current native interface. Keep it out of Git.

`PLAN_READY` (exit 0) is an offline subscription CLI plan, not execution or account
verification. `HOST_TOOL_READY` (exit 0) is a distinct plan for explicitly authorized
built-in host controls with supported native control evidence; billing remains unknown.
All failures use `BLOCKED` (exit 2). Both policy and catalog are strictly validated.
JSON duplicate keys and non-finite numbers are rejected.

Read [SKILL.md](SKILL.md) for the workflow and [schema](references/schema.md) for
configuration. Examples use fictitious providers and models, not product claims.
No paid API, usage-credit fallback or paid Fast mode is supported. Subscription
CLI access does not imply API access. Unknown quota is never reported as available.

## Installation, update and removal

Use the repository's installer and instructions when available. To install this
skill manually, copy this entire directory to an empty skill directory supported
by your host, then select it through that host's documented mechanism. The host's
location varies across Windows, macOS and Linux; the helper has no home-directory
or global-configuration dependency. Copying files does not activate a model.

For an update, stage the new directory separately, run its tests and review the
schema migration below. Preserve your private configuration separately. Replace
only the installation you own, retaining a backup until verified. To remove, remove
only this installed skill directory and any reference you explicitly added. This
package does not edit global instructions, authentication or installed provider CLIs.

## Migration and coverage

This public candidate derives from the private 1.0.1 deterministic planner. It
retains scoring, risk floors, numeric ordering, freshness and distinct requested /
resolved / effective settings. It intentionally replaces personal family rankings,
provider-specific ID parsers and defaults with explicit policy and catalog schemas.
The prior catalog schema 1 is not accepted; use catalog schema 2 plus policy schema 1.
Model identity/version mapping now requires inspection of the referenced native
source; a generic helper cannot authenticate arbitrary provider naming conventions.
No original installer or personal release attestations are copied.

Current local validation: Windows, Python 3.12, offline standard-library tests.
No live provider, macOS or Linux runtime has been tested for this candidate.
Paths use `pathlib`, but that is not a cross-platform test result. See repository
release evidence for the final command, count and exact package digest.

Source package 1.0.1 carried no standalone LICENSE in the inspected skill directory.
This public candidate is covered by the repository's MIT license, with publication
authorized by the source owner. See the root license and notices for attribution.
