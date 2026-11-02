# Portable Council 公開版 0.1.0

[English](README.md) · [Adapter 協定](ADAPTERS.md)

此工具使用經使用者授權、由操作者檢查的訂閱 CLI adapter 收集第二意見，保留逾時、取消、有限重試與部分成功結果。它不提供已驗證的原生供應商整合，也不猜測最新模型 ID。

## 安裝、更新、移除

需要 Python 3.10 以上，僅使用標準函式庫。將整個技能目錄複製到代理支援的技能位置，或使用專案根目錄的安裝工具。設定與收據放在技能外。更新時先在新目錄執行測試，保留自己的設定，再替換舊版。移除時僅刪除自行安裝的技能目錄與自行選擇的執行資料；不會安裝服務或修改全域設定。

在本技能目錄執行：

```text
python -B -m unittest discover -s tests -p "test_*.py" -v
python -B examples/demo.py --run-dir /path/to/new-offline-demo
python -B scripts/council.py status --config examples/council.example.json
```

請換成作業系統上的實際新路徑。離線範例只呼叫本機 Python 假席位，預期結果為 `partial`：一席成功、一席失敗。範例中的證據明確是合成測試資料，不能用於真實帳號。預設設定停用所有席位，所以 `status` 回傳 blocked 與 exit 2 是正常的。

## 使用流程

可設定內附的 [原生 JSON bridge](NATIVE_BRIDGE.md) 來使用已檢視的 Claude Code 結果協定，不必自行寫 adapter；其他供應商則依 [ADAPTERS.md](ADAPTERS.md) 實作並審查。確認已安裝 CLI 支援的參數、登入方式與原生限制。CLI 訂閱和付費 API 是不同計費路徑；不得因訂閱不足改用 API、credits 或 overage。此版一律 Fast off，沒有真實供應商驗證。

複製範例設定，填入精確原生模型 ID、effort、模式與 24 小時內的原生帳號證據。證據必須涵蓋已登入、訂閱資格、全部設定及可用額度。額度未知保持 unknown 並停止該席；只有原生介面確認可用、卻不顯示數值時才允許 `remaining=null` 搭配 `available`。不要讀取或匯出憑證內容。

`status` 與 `plan` 只驗證本機設定及 executable，不呼叫供應商，也不能證明證據屬實。確認使用者授權問題、資料及席位後，再執行：

```text
python -B scripts/council.py plan --config /path/to/council.json
python -B scripts/council.py ask --config /path/to/council.json --question-file /path/to/question.txt --run-dir /path/to/new-run --cancel-file /path/to/cancel.marker
```

路徑由操作者配置，沒有個人 Windows 路徑。相對 adapter 參數以設定檔所在目錄為基準。使用 argv、不拼接 shell 指令；腳本需明確透過已審查的 interpreter 執行。

## 執行與結果

最多 8 席，每席總期限 0.1–600 秒，預設 180 秒與一次嘗試。明確設定 `max_attempts=2` 才允許一次重試，且僅限已終止、可確定尚未開始推論的 transient 失敗。登入、額度、政策、未知、逾時與取消不會自動重試。不會自動換模型、總結或加開辯論回合。

Ctrl+C 或建立指定 cancel marker 可取消未完成席位；既有成功答案仍保留。僅清理本次程序群組／Windows Job；Windows 程序先暫停並納入 Job 才恢復，視窗隱藏，清理會等待所屬程序 handle 結束。這不是作業系統沙箱，adapter 不應自行脫離程序管理。清理最多另需一秒，無法確認時保留 `cleanup_incomplete:true`，不可忽略並直接重試。

保留 `job_id`、`receipt.json`。檔案鎖阻擋主收據更新時會寫入 `recovery.json`；以相同 job_id 和較新的 updated_at 判斷版本，不把舊的 running 視為完成。每次使用新執行目錄，拒絕覆寫既有目錄。全部成功為 completed、部分成功為 partial、無成功為 failed，逐席另列 blocked、timeout、cancelled 等狀態。部分成功的 CLI exit code 是 2。

只有原生最終結果、正確身分、非空完整答案與 exit 0 才算完成。`requested`、`resolved`、`effective` 分開保存；未暴露的 effective 欄位維持 null。多模型意見一致不等於查證成功。

## 限制與授權

此公開版抽出有界程序執行器，重寫成小型 adapter 協定，不是私人版 MCP、背景工作介面、固定席位與 CLI 參數建構器的相容替代品。沒有提供這些原介面。需背景管理時，請使用自己的程序管理工具搭配同一份收據。

本機只實測 Windows／Python 3.12 與合成子程序。Linux/macOS 程式路徑需待 CI 實跑，不能稱為已驗證；未呼叫真實模型或驗證任何帳號、額度、模式與計費。環境變數 allowlist 排除 API keys，但 adapter 仍可讀取該使用者可用的 CLI 設定／登入存放區，不能宣稱完全隔離憑證。請審查 adapter，保留平台權限限制，只送必要資料。

保留來源 MIT 授權與 hex copyright，詳見 [NOTICE](NOTICE.md) 及 [LICENSE](LICENSE)。
