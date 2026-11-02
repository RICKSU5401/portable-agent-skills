# Portable Agent Skills

[English](README.md)

三個可配置的技能，分別處理模型分級規劃、額度不足時的工作交接，以及多席位意見收集。目前為 **MIT 授權發布候選版**；既有第三方程式保留原授權與署名，見 [授權說明](docs/LICENSING.md)。

| 技能 | 實際功能 | 使用邊界 |
| --- | --- | --- |
| [tiered-model-routing](skills/tiered-model-routing/SKILL.md) | 依複雜度、不確定性、影響評分，使用政策與能力目錄規劃模型 | 不會改變目前對話模型，也不呼叫推論 |
| [quota-aware-handoff](skills/quota-aware-handoff/SKILL.md) | 額度與交接預算檢查、檔案快照、接手及回交接驗證 | 不會憑空取得供應商額度；未知保持未知 |
| [portable-council](skills/portable-council/SKILL.md) | 管理 adapter 程序、期限、取消及部分席位結果 | 需自行提供可信的原生 CLI adapter 與能力證據 |

沒有寫死任何「最新」模型或全球適用的家族排名。請依自己帳號原生介面提供的精確 ID、模式能力與訂閱資格設定。範例與離線測試都是合成資料，不能當成真實帳號的能力或額度證據。

Council 附有預設停用、可配置的 Claude Code 終態 JSON 轉接器，這條路線可透過設定使用，不必自行撰寫 adapter。啟用前請核對 [轉接說明](skills/portable-council/ADAPTERS.md)、自己的 CLI 參數與帳號證據；其他供應商仍需受審閱的 adapter。內附轉接器僅通過離線測試，尚未進行真實模型呼叫驗證。

## 相依條件與測試

使用 Python 3.10+ 標準函式庫；本次實測為 Windows / Python 3.12。需要能載入 `SKILL.md` 目錄的宿主。若要真正呼叫模型，需另行安裝並登入可信任的原生 CLI，再設定已授權的 adapter。Chat 訂閱與 API 計費是不同路徑；本專案不啟用 API 備援、超額扣款、購買額度或付費 Fast。部分交接操作需要硬連結與原子檔案操作，不支援時會停止。

在專案目錄執行：

```text
python -B scripts/check_release.py
python -B scripts/run_tests.py
```

檢查器只協助發現常見私人資料，不能保證任意內容適合公開。離線測試通過不代表真實供應商、額度或跨平台已通過，詳見 [測試範圍](docs/TESTING.md)。

## 安裝、更新、移除

先建立測試技能目錄，再用宿主文件指定的目錄安裝。請使用實際檔案系統路徑：安裝器會拒絕符號連結、Windows junction 及含連結的上層目錄。Windows PowerShell：

```powershell
New-Item -ItemType Directory -Force -Path ./sandbox-skills
python -B scripts/manage_skills.py install tiered-model-routing --root ./sandbox-skills
python -B scripts/manage_skills.py install tiered-model-routing --root ./sandbox-skills --apply
```

macOS/Linux 命令範例（未在本機實測）：

```sh
mkdir -p ./sandbox-skills
python3 -B scripts/manage_skills.py install tiered-model-routing --root ./sandbox-skills --apply
```

另外兩個技能改用 `quota-aware-handoff` 或 `portable-council`。不加 `--apply` 只預覽及驗證。工具不猜測各平台的全域路徑、不修改全域指令，也不覆蓋既有未受管理的個人技能。宿主載入方式需依宿主支援的 UI 或設定操作；技能文字本身不會切換現有對話模型。

更新時先審閱新版，再於新版目錄執行：

```text
python -B scripts/manage_skills.py update tiered-model-routing --root ./sandbox-skills --apply
python -B scripts/manage_skills.py remove tiered-model-routing --root ./sandbox-skills --apply
```

更新／移除都會拒絕已被本機修改的安裝，並保留舊版於唯一的 `.skill-backup-*` 同層目錄。輸出包含備份位置。若宿主也掃描隱藏目錄，請將備份移到宿主不載入的地方。要還原時先確認原安裝位置為空，再移回備份的技能目錄；確認不需備份後才手動移除。工具沒有遞迴清除指令。

請將真實額度快照、模型目錄、adapter 設定與執行收據放在本倉庫以外，不提交對話、帳號 ID、憑證或原生記錄。

## 限制與安全

請分開記錄 requested、resolved、effective；計畫、exit code 或模型自述不等於原生執行證據。額度／帳務未知或過期時，不視為可用容量，應保留交接預算並停止依賴該證據的工作。

交接雜湊用來偵測檔案變更，不證明身分、授權或所有寫入程序已停止。Adapter 是以你的權限執行的程式，請先審閱；不要把模型輸出當成 shell 指令。重試不得擴張權限、繞過登入、改用付費服務或偷偷降級模型。Council 保留成功席位，但多模型同意不等於事實已查證。

公開介面與個人版不同，請閱讀 [遷移說明](docs/MIGRATION.md)。本次沒有外部模型推論實測，沒有背景排程或自動升級。問題回報請勿貼私人記錄；見 [SECURITY.md](SECURITY.md)。
