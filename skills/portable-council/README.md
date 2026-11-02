# Portable Council — public edition 0.1.0

[繁體中文](README.zh-TW.md)

A small Python runner for explicitly authorized second opinions through operator-reviewed subscription CLI adapters. It preserves partial results, bounds process lifetime and records unknown capability fields honestly. It does not select a current model for you. An opt-in [native JSON bridge](NATIVE_BRIDGE.md) supports the inspected Claude Code result contract; live provider integration remains unverified.

## Install and try

Requires Python 3.10+; no packages, API keys, shell plugins or background service are installed. Copy this entire directory to a skill directory supported by your agent, or use the repository's installer. Keep configuration and receipts outside the installed skill. To update, stage the new directory, run its tests, then replace your previous copy only after preserving local configuration. To remove, remove only the skill directory you installed and any run directories you choose to discard; there are no global registrations to undo.

From this directory:

```text
python -B -m unittest discover -s tests -p "test_*.py" -v
python -B examples/demo.py --run-dir /path/to/new-offline-demo
python -B scripts/council.py status --config examples/council.example.json
```

Choose an actual unused path on your OS, for example a directory under your temporary directory. The demo starts only local Python programs and expects `partial`: one synthetic answer, one synthetic failure. Its evidence values are deliberately fabricated test fixtures and must never be used for a real account. `status` exits 2 because the shipped seat is disabled. No command above uses an AI service.

## Real use

1. Configure the [native JSON bridge](NATIVE_BRIDGE.md) for its supported Claude Code contract, or implement another adapter following [ADAPTERS.md](ADAPTERS.md). Use the installed provider's supported native CLI and authentication. Review the adapter and inherited CLI settings for API/credit/overage/model fallback and permissions. The runner cannot enforce billing inside arbitrary trusted executable code.
2. Copy `examples/council.example.json` outside the installed directory. Fill the canonical host/provider IDs, adapter argv, requested model/effort/modes and native evidence; enable only authorized seats. No default model IDs are provided.
3. Run `plan`, inspect per-seat results, then `ask`. `plan` and `status` perform local declaration validation and executable discovery, not provider calls. Evidence is not cryptographically authenticated by this runner.

```text
python -B scripts/council.py plan --config /path/to/council.json
python -B scripts/council.py ask --config /path/to/council.json --question-file /path/to/question.txt --run-dir /path/to/new-run --cancel-file /path/to/cancel.marker
```

All paths may be Windows, macOS or Linux paths. Relative adapter arguments are interpreted relative to the config file's directory. Executable paths are resolved there or on PATH. Commands use argv and `shell=False`; use an executable interpreter for a reviewed script. `.bat`, `.cmd` and `.ps1` cannot be implicit executables. No provider flags are constructed or guessed by this package.

Native subscription access and API access are separate billing routes. Evidence must declare `billing=subscription`, authenticated eligibility, exact settings, and available quota; null/unknown/exhausted quota blocks the seat. `remaining=null` is allowed only when native evidence confirms capacity available without exposing a number. Additional paid Fast and API routes are unsupported. Requested settings, resolved invocation and native effective metadata are recorded separately. An adapter must never infer effective settings from requested arguments.

## Deadlines, cancellation and results

Each of at most 8 seats has one 0.1–600 second deadline across all its attempts. Defaults: 180 seconds, one attempt. `max_attempts=2` explicitly opts into one retry for a terminal `transient` failure with `inference_started=false`. All other failures stop. A timeout is an uncertain inference outcome and is never automatically replayed. No fallback models, extra debates or automatic paid calls.

Ctrl+C or creating the chosen cancel marker cancels running seats and preserves completed ones. A preexisting marker prevents process creation. Only the invocation's process groups/Windows job are cleaned up; this is process ownership, not a sandbox. Adapters must not detach or escape that ownership. On Windows, subprocesses start hidden and suspended until attached to their Job. Cleanup waits owned process handles and may take up to one additional second. Any unconfirmed cleanup is reported as `cleanup_incomplete:true`, including on timeout/cancellation; investigate it before another attempt.

`receipt.json` contains `job_id`, per-seat attempts and terminal statuses. If replacing it is blocked by a file lock, `recovery.json` preserves the latest checkpoint. Compare `job_id` and `updated_at` before choosing between them; do not treat a stale primary `running` receipt as a final result. Keep both when investigating failures. The runner creates a new directory and refuses to overwrite an existing run.

Council status is `completed` only if all seats complete, `partial` if at least one completes, otherwise `failed`. Seat status also distinguishes `blocked`, `timeout`, `cancelled` and `failed`. CLI exit 0 means ready/all completed; exit 2 means blocked/partial/failed/error. A blocked seat does not prevent separately authorized, ready seats from answering. No automatic summary spends more quota.

## Scope and safety

Only pass the question and approved reference material. Prompts and adapter stdout are not shell commands. Known inherited API-key variables are excluded by an environment allowlist, but CLI config/auth stores remain accessible to the reviewed adapter; this is not a credential-isolation guarantee. Adapters and their configuration are executable trust boundaries. The runner does not grant permissions, disable provider restrictions, install CLIs or contact any service itself.

The public edition intentionally replaces private provider launchers with an explicit adapter protocol. It is not a drop-in replacement for the source's MCP server, native CLI argument builders, background job interface, installation registry or fixed roster. It does not include those interfaces or claim their verification. Use foreground receipts with your own process supervisor if needed; no background daemon is bundled.

Local verification: Windows / Python 3.12, synthetic subprocess tests only. Linux/macOS process paths are implemented but must pass CI before claiming verified support. No real model inference, subscription, model availability, effective mode or billing was verified for this edition. See [NOTICE](NOTICE.md) for source attribution and [CHANGELOG](CHANGELOG.md).
