# Migrating from the personal packages

Stage this edition separately and review its examples before replacing an existing installation. The installer refuses to overwrite an unmanaged skill. Existing global instructions, scheduled tasks, native CLI configuration, source repositories and installed personal skills are outside this release's migration scope.

| Component | Source lineage | Public interface change |
| --- | --- | --- |
| tiered-model-routing | Personal 1.0.1 deterministic helper | Public 2.0.0-rc.1 uses policy schema1 and catalog schema2; explicit family/effort/mode choices replace fixed personal provider ranks. Host-native mode is separate from subscription CLI evidence. |
| quota-aware-handoff | Personal quota/checkpoint/dispatch helpers and later return protocol | Public 0.1.0 uses generic profile identifiers, default disabled configuration and a read-only return inspector. The return inspector is newly implemented against the documented protocol. |
| portable-council | Local Portable0.5.2/peer runtime1.2.2 derived from MIT upstream | Public0.1.0 retains bounded process helpers and implements an adapter contract plus an opt-in configurable Claude terminal-JSON bridge; private launchers, MCP, background jobs and fixed model roster are not included. |

Model IDs, account observations, project allowlists, native command flags and quota snapshots must be supplied by the operator. Do not reuse old evidence just because its JSON parses. Resolve exact models and modes through the relevant native interface, retain ordinary permissions, and independently check the billing route.

For built-in host agent tools that do not expose billing fields, routing supports explicit `host_native` configuration with `host_control_authorized=true` and fresh native-control evidence. It returns `HOST_TOOL_READY`, preserves unknown billing/quota, and does not assert a confirmed subscription. This is not permission to substitute an API or CLI.

Handoff and Council need trusted process/adapter implementations for actual provider calls. Their offline protocol verification does not authorize inference, authenticate incoming files, or prove that provider quotas were checked. Never migrate private logs or original Git history into the public repository.

繁體中文：請先獨立安裝並審閱範例，公開版不會自動升級你的個人安裝或全域指令。Routing 的資料格式已改版；Council 改採需自行審閱的 adapter 介面，不是原本 MCP／背景工具的直接替代。Handoff 保留既有核心，回交接檢查器則依文件協定重新實作。所有真實模型、帳號、計費、額度、權限與平台相容性，仍需使用者依自己的原生環境確認。
