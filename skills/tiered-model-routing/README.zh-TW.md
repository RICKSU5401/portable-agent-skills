# 模型分級路由

[English](README.md) · 版本 **2.0.0-rc.1**

這是使用 Python 標準函式庫的規劃器，依任務難度、原生模型能力與訂閱邊界產生計畫。
它不會發送提示、啟動模型、替目前對話切換模型或修改全域設定。供應商、家族、模型
ID、effort 優先序及模式要求都來自使用者設定及當次原生證據，不內建「最新型號」。

## 安全範例與驗證

需要 Python 3.10 以上。在本技能目錄執行：

```text
python -B scripts/route.py --provider example-provider --complexity 2 --uncertainty 2 --impact 1 --policy examples/policy.json --catalog examples/catalog.fixture.json
python -B -m unittest discover -s scripts -p "test_*.py" -v
```

範例必須回傳 `BLOCKED`、退出碼 2：它使用虛構名稱、過期時間、未登入狀態，計費與
額度均未知。請實際查核自己的原生介面，再另建本機 catalog；不可只改範例的標籤或
時間就視為驗證完成。私人 catalog 不應提交至 Git。

`PLAN_READY`、退出碼 0 只代表訂閱 CLI 的離線計畫成立，不代表帳號或推論已驗證。
明確啟用 `host_native`、已有使用者授權且有新鮮原生控制證據時，內建代理工具可回傳
獨立的 `HOST_TOOL_READY`。這條路線保留未知計費與額度，不把它們宣稱為已確認訂閱，
也不能拿來替額外付費 API 或 CLI 解套。失敗均回傳 `BLOCKED`。

詳見 [工作流程](SKILL.md) 與 [設定格式](references/schema.md)。訂閱 CLI 與另計費
API 是不同服務；此版不支援付費 API、usage credits 或付費 Fast。CLI 範例預設關閉
Fast，未知額度不會被填成可用。每個啟用模式都需明確設定、能力證據及訂閱包含證據。

## 安裝、更新、移除

優先使用專案根目錄的安裝器與說明。手動安裝時，將整個技能目錄複製到宿主支援的
空技能目錄，再透過宿主文件指定的機制載入。Windows、macOS、Linux 的位置由宿主
決定；本程式不寫死使用者家目錄，不修改原生 CLI、登入或全域指令。

更新先放到另一個目錄、執行測試、檢查格式遷移，再替換自己管理的安裝並保留備份。
私人設定另外保存。移除時僅移除本技能安裝目錄，以及自己明確加入的引用。

## 來源與限制

此公開候選版源自私人 1.0.1 規劃器，保留難度分數、風險下限、數字版本排序、證據
時效、requested / resolved / effective 分離。個人模型排名與供應商專屬 ID 解析已
改為明確設定；舊 catalog schema 1 不相容，請使用 catalog schema 2 與 policy schema 1。
模型 ID／版本對應需要檢視原生來源；通用程式無法認證任意供應商的命名規則。

目前僅實測 Windows、Python 3.12 的離線測試；沒有對本候選版完成原生模型、macOS 或
Linux 實測，不將路徑可配置宣稱成跨平台已驗證。最終測試數量與套件摘要以根目錄驗收
紀錄為準。原安裝技能目錄未附獨立 LICENSE；來源所有者已授權此公開版採用專案根目錄的
MIT 授權，請一併保留授權及來源聲明。
