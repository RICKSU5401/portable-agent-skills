# Source attribution

The local source package identifies its upstream as [hex/claude-council](https://github.com/hex/claude-council), MIT, copyright (c) 2025–2026 hex. Its recorded base ref was `5e4267ceddd7f316d2e508db894141ffe9621950`; its local additions and modifications are not claimed to exist at that public upstream commit.

This public edition adapts `scripts/process_runner.py`, `scripts/process_tree.py` and most of `tests/test_process_runner.py` from the local Portable 0.5.2 source. Windows processes are created hidden and suspended, attached to the owned Job before their primary thread is resumed, and covered by additional lifecycle tests. Cleanup now waits handles belonging to that Job, preserves bounded deadlines and reports unconfirmed cleanup explicitly. Optional inherited POSIX process groups keep nested bridges under their parent's ownership. Test imports were adapted. The complete upstream MIT notice is retained in [LICENSE](LICENSE).

`scripts/council.py`, council protocol tests, examples and public-edition documentation are new general-purpose adaptations of lifecycle requirements. `scripts/native_json_bridge.py` is a new configuration-driven adapter using the source-observed Claude result schema, with public documentation cross-checks and synthetic tests. They do not copy private model defaults, account receipts, conversation history or the source repository's Git history. The local source's provider engine and peer runtime (1.2.2) were not redistributed.

Version 0.1.0 is an independent public-edition version, not an upstream release or a native-platform certification.

The Windows launch sequence uses the documented [CREATE_SUSPENDED flag](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags), [thread ownership enumeration](https://learn.microsoft.com/en-us/windows/win32/toolhelp/traversing-the-thread-list) and [ResumeThread](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-resumethread). The runner resumes only the single initial thread owned by the process it just attached; ambiguous ownership fails closed.
