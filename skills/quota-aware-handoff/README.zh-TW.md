# 額度感知交接

[English](README.md) · 公開候選版 0.1.0

在額度耗盡前保存可驗證成果，把一個已授權工作單元交給接手者，再回到原對話核對結果。
需要 Python 3.10 以上，僅用標準函式庫。預算檢查、快照與回傳檢查不連網、不讀憑證、也不呼叫模型。

## 功能與界線

- `quota_guard.py`：依全部適用額度視窗、成本上界、交接保留額度與誤差餘裕，決定是否可開始下一單元。未知、過期或資料不完整一律停止擴展。
- `handoff.py checkpoint/verify`：保存明確選取的檔案與 SHA-256，核對快照／目前檔案。雜湊一致不代表測試真的執行或產品正確。
- `handoff.py inspect-return`：用原對話獨立保留的來源 ID／雜湊，核對回傳的 origin、task、unit、receipt。通過僅表示可開始整合審查；不自動合併、刪檔、解鎖或宣稱完成。
- `dispatch.py plan`：只讀預檢。只有 `launch --execute` 會啟動已設定且獲授權的原生 CLI。每個 handoff ID 跨所有接手者只允許一次實際嘗試，先 ACK，再等待 permit。

## 安裝、更新、移除

依專案根目錄說明安裝此完整技能目錄；也可複製到宿主正式支援的 skills 路徑。
不需要修改全域指令即可執行 helper。更新先在新目錄驗證並備份舊版，保留個人設定。
移除前停止相關寫入者，只移除自己安裝的技能副本；專案 `.ai-relay` 紀錄與外部授權檔先保留，另行審查。
Windows、macOS、Linux 路徑由呼叫者提供；Windows JSON 路徑請使用跳脫反斜線或 `/`，有空白時在 shell 加引號。

## 本機使用

以下命令在技能目錄執行，系統若使用 `python3` 請替換命令名稱。

```text
python scripts/quota_guard.py evaluate --snapshot assets/quota.snapshot.example.json --unit assets/next-unit.example.json
```

範例刻意保留未知值，應回傳 `CHECKPOINT_AND_HANDOFF`／exit 2。不得為了通過而填入虛構額度。
每個視窗都需滿足 `餘額 >= safety_factor × 下單元成本上界 + 交接保留額度 + 誤差餘裕`。
預設 safety factor 1.5 可設定；不是供應商保證。保留額度包含保存、驗證、接手確認與回傳報告。
context 與訂閱額度不同；現有 50/70/90% 提醒只套用已知且不超過 500,000 tokens 的有效視窗，並不更改宿主壓縮觸發器。

填寫 `assets/HANDOFF.template.md` 和驗證索引，確定寫入者停止後建立快照。
完整命令與 ACK／permit 欄位見 [protocol](references/protocol.md)。原對話獨立保存來源 ID／雜湊與 origin_ref。
接手者完成、失敗、取消或受阻時，都應保存實際結果與 `return-receipt.json` 到新快照。
回傳流程見 [return-handoff](references/return-handoff.md)。原對話用原先保留的 pins 執行 `inspect-return`，
再比較來源、回傳與目前檔案，核對實際測試、衝突、權限及寫入者，才可記錄已整合。

## 訂閱與啟動設定

`assets/relay.config.example.json` 全部預設停用，放在專案外，由操作者依既有授權填寫。
provider 名稱、精確模型、原生 executable、argv、專案及 task allowlist 都由設定提供。
不硬編碼「最新模型」。模型／effort／模式需另由已驗證的原生能力證據決定。
本工具只允許訂閱模式，要求登入與帳務新鮮證據，Fast 明確關閉；API 另行計費，不是訂閱 CLI 額度。
不自動開 API、usage credits、overage、付費 Fast，也不繞登入或權限。設定中的確認欄位是操作者聲明，
無法證明任意 executable 或繼承的 CLI 設定安全；啟用前必須自行核對。

## 測試與限制

```text
python -B -m unittest discover -s scripts -p "test_*.py" -v
```

測試使用合成額度、暫存檔與有期限的假接收程序；不呼叫真實模型。本次本機驗證目標是 Windows／Python 3.12，
不能外推 macOS、Linux 或真實供應商已測通。Windows 無符號連結權限時相關測試可能略過。
額度 normalizer 只解析原始版本支援的資料形狀，不是即時蒐集器；新 CLI 版本需要重新驗證。
permit 需要同檔案系統硬連結；不支援時在啟動前停止。ACK 上限 60 秒，真實模型可能無法在期限內完成。
逾時不會自動重試或殺程序，也不能證明接手者已停止；須保留 lock／attempt 並由操作者核對程序與外部副作用。

檔名過濾不是機密內容掃描；分享前須檢查實際內容。沒有跨主機鎖、常駐監測、自動上傳、自動喚醒原對話、
自動整合帳本或敵對程序防護。保留 `prepared`、`delivered`、`RETURN_VERIFIED`、`RETURN_INTEGRATED` 的差別。
