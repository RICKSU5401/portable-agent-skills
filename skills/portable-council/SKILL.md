---
name: portable-council
description: Plan and run explicitly authorized second-opinion seats through reviewed subscription CLI adapters. Keep capability evidence, bounded deadlines, cancellation, limited retries, and partial results explicit.
license: MIT
---

# Portable Council

Version: 0.1.0 public edition. Read [README](README.md) and the [adapter contract](ADAPTERS.md) before using a real provider. [繁體中文](README.zh-TW.md).

1. Confirm the user authorized the question, selected peers and data to send. Loading this skill does not authorize contacting other agents. Send only necessary material; do not forward entire conversations, secrets or private history.
2. Use canonical provider IDs and exclude the host from the roster. Do not run when `COUNCIL_ACTIVE=1`. A renamed alias must not be used to include the host.
3. Configure reviewed adapter executables, exact account-visible model IDs, supported effort and modes. Never invent a latest model ID or substitute a model to make a request succeed. Council has its own configured roster; routing changes are not automatically applied to it.
4. Gather fresh (within 24 hours), authenticated native capability evidence for the same non-secret account alias. Confirm subscription eligibility, all requested settings, Fast off and available quota. Unknown quota remains unknown and blocks that seat. Do not read or export credential contents.
5. `status` and `plan` validate local configuration only. They do not log in, probe account permissions, prove billing or verify execution. Treat supplied evidence as an operator declaration requiring source inspection.
6. Run `ask` only after authorization and preflight. Default one attempt per seat. Setting `max_attempts=2` explicitly opts into one retry, only for a terminal transient failure that declares inference never started; never retry authentication, quota, policy, unknown, timeout or cancellation failures. No automatic fallback, synthesis or debate round.
7. Use a fresh run directory and keep `job_id`. Poll that run's receipt; `running`, fragments and worker activity are not completion. A completed seat needs a terminal response, matching identity, nonempty answer and successful process exit. Effective fields not exposed by the native provider stay null; never copy requested settings into effective metadata.
8. Preserve successful seats when others fail. Report requested/resolved/effective settings, answer attribution, failed/blocked seats and remaining unknowns. Agreement between models is not fact verification. Do not expose hidden chain-of-thought.

Python 3.10+, standard library only. Commands run from the skill directory:

```text
python -B scripts/council.py status --config examples/council.example.json
python -B scripts/council.py plan --config /path/to/council.json
python -B scripts/council.py ask --config /path/to/council.json --question-file /path/to/question.txt --run-dir /path/to/new-run --cancel-file /path/to/cancel.marker
```

The bundled configuration is disabled and intentionally reports blocked. Native subscription CLI authentication is distinct from paid API access. This edition has no API client, billing activation, paid Fast or bypass-login mode. A configurable [native JSON bridge](NATIVE_BRIDGE.md) supports the inspected Claude Code result contract without requiring custom adapter code; other interfaces need reviewed adapters. Live provider integration remains unverified. Adapters are trusted executable code; this runner is not a security sandbox.
