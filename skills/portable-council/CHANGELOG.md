# 0.1.0

- Extract bounded subprocess execution and owned process-tree cleanup with MIT notice.
- Introduce a configurable subscription CLI adapter contract, disabled example roster and no hardcoded model IDs.
- Validate fresh native capability declarations; block unknown quota, API billing and Fast.
- Retain separate requested/resolved/effective fields, terminal response checks, timeout, cancellation, explicit limited retries and partial results.
- Reject duplicate JSON keys and non-finite numbers in configuration and adapter responses; revalidate evidence before every attempted dispatch.
- Close the Windows launch-before-Job race by creating suspended, attaching ownership, then resuming the verified primary thread; test assignment failure, cancellation and deadline cleanup while suspended.
- Wait Job-owned process handles during bounded cleanup and retain cleanup_incomplete on uncertain termination; keep nested POSIX bridge processes under their Council parent.
- Reject effective model/mode/billing contradictions on failure responses before considering retry, and reject ambiguous protocol versions and duplicate Fast mode fields.
- Add a disabled, operator-configured native JSON bridge for the inspected Claude Code terminal result contract, with exact settings bindings and synthetic subprocess tests. No live-provider compatibility is claimed.
- Add bilingual documentation and offline synthetic process tests. Native providers and Linux/macOS runtime coverage remain unverified.
- This is a new public interface; the source MCP/background/provider-specific interfaces are not included.
