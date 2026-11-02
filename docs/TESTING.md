# Verification and limits

Run all offline tests with `python -B scripts/run_tests.py` from the repository root. The runner uses separate processes for each suite and prevents bytecode writes. It requires no dependency download and does not call real model providers.

Covered behaviors include numeric model versions and difficulty floors; stale/unknown/conflicting capability evidence; explicit host-native routing; quota reserve accounting; immutable checkpoints; ACK/permit publication; return origin/source/hash binding; cancellation/timeouts/partial seat results; bounded retries; and reversible managed installation.

Local validation is Windows / Python3.12.10. Some symlink tests can be skipped when the OS does not grant symlink privileges. The handoff dispatcher requires hardlinks for atomic permit publication. A restricted sandbox that denies hardlinks blocks those integration tests before launch; it does not justify weakening the runtime's atomicity requirement.

GitHub Actions is configured for Windows, Ubuntu and macOS with Python3.10/3.12. A configured matrix is not a test result. Only a completed, successful workflow run establishes the tested matrix for its exact commit. Local evidence and eventual CI status must be reported separately.

No live-provider inference, account eligibility, effective mode, CLI integration or real quota-triggered handoff has been verified for this public edition. Synthetic samples never establish native account evidence. `check_release.py` is a heuristic accidental-data scan, not a comprehensive security audit.

繁體中文：請執行根目錄的離線測試入口；不需下載套件，也不呼叫真實模型。本機驗證範圍為 Windows／Python3.12.10。符號連結測試可能因系統權限略過，硬連結不支援則會阻擋原子交接。CI 矩陣設定不代表已通過，應以該 commit 的實際工作結果為準。真實供應商、帳號、計費、有效模式及額度觸發接手均未在公開版實測。
