# Portable Agent Skills

[繁體中文](README.zh-TW.md)

Three configurable skills for planning model use, preserving work when quota runs low, and collecting independent model responses. This is a **release candidate**, licensed under MIT. Retained third-party material keeps its own license and attribution; see [licensing](docs/LICENSING.md).

| Skill | What runs locally | Boundary |
| --- | --- | --- |
| [tiered-model-routing](skills/tiered-model-routing/SKILL.md) | Difficulty scoring and deterministic route planning against a supplied policy/catalog | Does not change the current model or launch inference |
| [quota-aware-handoff](skills/quota-aware-handoff/SKILL.md) | Quota/reserve checks, file checkpoints, receiver and return verification | Does not discover arbitrary account quotas; unknown remains unknown |
| [portable-council](skills/portable-council/SKILL.md) | Bounded adapter processes, timeout/cancel handling and partial-seat receipts | Requires trusted provider adapters and independently checked native capabilities |

No provider/model family is universally ranked or assumed available. Configure the exact IDs, capabilities and subscription eligibility exposed by your account. Examples and offline tests use synthetic data, not current model recommendations or account evidence.

Council includes an opt-in, configurable bridge for the inspected Claude Code terminal JSON contract, so that route can be configured without writing adapter code. Review the [bridge instructions](skills/portable-council/ADAPTERS.md), native CLI arguments and account evidence before enabling it. Other provider contracts require a reviewed adapter. The bundled bridge has offline tests; it has not been verified through live model calls.

## Requirements

- Python 3.10+ and the standard library. This candidate is locally verified on Windows with Python 3.12; see [testing](docs/TESTING.md) for the actual coverage and gaps.
- A host that loads a `SKILL.md` instruction directory. Loading instructions is separate from executing helpers.
- For optional real inference: a separately installed, trusted native CLI, its normal login, a confirmed subscription route, and an authorized adapter. API access can incur a separate bill even when you have a chat subscription. This project does not enable API fallback, overage, purchased credits or paid Fast.
- A filesystem supporting ordinary files and atomic operations. Some handoff operations additionally need hardlinks; unsupported operations fail closed.

There is no background service, automatic provider login, registry installer, scheduled task or automatic model upgrade.

## Inspect and test

From this repository directory:

```text
python -B scripts/check_release.py
python -B scripts/run_tests.py
```

The release check is a heuristic for accidental private data and package layout. It is not proof that arbitrary content is safe to publish. Read configuration, diffs and license notices before publication or running a new adapter. Offline test success does not prove real provider access.

## Install, update and remove

Choose an empty test skills directory first; later choose the directory documented by your host. The installer accepts any explicit directory and does not guess Windows/macOS/Linux host paths, edit global instruction files, or replace an unmanaged personal skill.

PowerShell example:

```powershell
New-Item -ItemType Directory -Force -Path ./sandbox-skills
python -B scripts/manage_skills.py install tiered-model-routing --root ./sandbox-skills
python -B scripts/manage_skills.py install tiered-model-routing --root ./sandbox-skills --apply
```

POSIX shell example:

```sh
mkdir -p ./sandbox-skills
python3 -B scripts/manage_skills.py install tiered-model-routing --root ./sandbox-skills
python3 -B scripts/manage_skills.py install tiered-model-routing --root ./sandbox-skills --apply
```

Repeat with `quota-aware-handoff` and `portable-council` when desired. Without `--apply` each action is a validation-only preview. Configure the host to load these directories using its supported UI/configuration. An instruction file cannot switch the model of an already running conversation.

After reviewing a new release, run from its unpacked directory:

```text
python -B scripts/manage_skills.py update tiered-model-routing --root ./sandbox-skills --apply
python -B scripts/manage_skills.py remove tiered-model-routing --root ./sandbox-skills --apply
```

Update/removal refuse an unmanaged or locally modified installation. Both preserve the old installation in a unique sibling `.skill-backup-*` directory and print its path. Keep that backup outside any host's active skill discovery path if your host scans hidden directories. Restore by moving the saved skill directory back only after checking the active destination is empty. Review and manually remove backups when no longer needed. The tool deliberately has no recursive purge command.

Install each skill as a complete directory. Keep your real catalogs, quota snapshots and adapter settings **outside this repository**. Do not commit receipts, conversation exports, account IDs, credential files or native provider logs. See each skill's references/examples for its configuration contract.

## Safety and limitations

- Requested, resolved and effective settings are distinct. A plan, exit code or model's self-description is not native execution evidence.
- Unknown/stale quota or billing is not free capacity. Preserve handoff budget and stop dependent work if evidence cannot be established.
- Handoff digests detect content changes; they do not prove identity, authorization or absence of running writers. Use a trusted transport and review the receiving task's permissions.
- Treat adapter executables as code with your user permissions. Do not run adapters from strangers or interpret untrusted model output as shell commands.
- Retry is bounded and does not authorize another bill, model downgrade, auth bypass or broader tools. Successful council seats remain usable when others fail; agreement is not factual verification.
- Public interfaces differ from the personal source versions. See [migration](docs/MIGRATION.md). Native providers have not been tested. Check the completed [CI runs](https://github.com/RICKSU5401/portable-agent-skills/actions) for each commit's operating-system coverage.

Contributions should include focused offline regression tests and update the relevant configuration documentation. Do not send real credentials or private session logs in issues. See [SECURITY.md](SECURITY.md).
