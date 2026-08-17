# 🛡️ E5 保活精靈

> 專為 GitHub Actions 打造的輕量版 Microsoft 365 E5 開發者訂閱保活工具。
> 每次執行都把「哪個帳號、跑了哪個 API、結果如何」完整推到你的 Telegram，
> 也可以隨時下指令強制立刻跑完某個帳號的全部 API。

<p align="center">
  <img alt="Python 3.13" src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white">
  <img alt="GitHub Actions" src="https://img.shields.io/badge/GitHub_Actions-2088FF?logo=githubactions&logoColor=white">
  <img alt="Telegram" src="https://img.shields.io/badge/Telegram-26A5E4?logo=telegram&logoColor=white">
  <img alt="相依套件 3 個" src="https://img.shields.io/badge/相依套件-3%20個-success">
  <img alt="version 1.8.1" src="https://img.shields.io/badge/version-1.8.1-blue">
</p>

> **第一次安裝請看 [安裝教學.md](安裝教學.md)** —— 七個步驟、全程瀏覽器完成、約 20 分鐘。
> 這份 README 是技術參考：參數、行為、成本、安全性。
> 版本之間改了什麼看 [CHANGELOG.md](CHANGELOG.md)。

---

## 目錄

- [特色](#特色) ・ [運作方式](#運作方式) ・ [每天實際會做什麼](#每天實際會做什麼)
- [Secrets 一覽](#secrets-一覽) ・ [E5_ACCOUNTS 格式](#e5_accounts-格式)
- [Telegram 指令](#telegram-指令) ・ [測試模式](#測試模式)
- [config.yml 參數](#configyml-參數) ・ [Actions 額度與成本](#actions-額度與成本)
- [安全性](#安全性) ・ [常見問題](#常見問題) ・ [檔案結構](#檔案結構) ・ [開發與測試](#開發與測試)

---

## 特色

| | |
|---|---|
| 📨 **每次執行都詳細回報** | 哪個帳號、跑了哪些 API、狀態碼、耗時、回傳摘要，逐項列給你看 |
| 🧪 **測試模式** | 一句指令就立刻跑完指定帳號的**全部 52 個** API，不抽樣、不延遲、逐項回報 |
| 🎭 **擬人化節奏** | 每天跑幾次會變、時段隨機、呼叫間隔叢發式、建立的東西不會 1 秒就刪 |
| 👥 **多帳號 + 帳號管理** | 停用／啟用／改名／移除單一帳號，不用重新授權其他帳號 |
| 🔑 **雙認證模式** | 委派權限（refresh token）與應用程式權限（client credentials）可混用 |
| 🔄 **Token 自動續命** | Microsoft 輪換 refresh token 後自動加密回寫 Secret，不用手動維護 |
| ☁️ **雲端授權** | 授權流程在 GitHub 上完成，不需要本機裝任何東西 |
| ⏳ **到期倒數提醒** | 自動抓訂閱剩餘天數，30／14／7／3／1 天時重點提醒 |
| 📈 **每週統計** | 成功率、最常失敗的 API、token 輪換次數，每週一自動推送 |
| ✅ **Verified commit** | 執行紀錄由 GitHub 自動簽章、顯示 Verified，不用管任何 GPG 金鑰 |
| 🧯 **失效不擴散** | 某個帳號 token 掛了 → 通知你並附修復步驟，其他帳號照跑 |
| 🔒 **為公開 repo 設計** | 公開日誌與提交檔案裡沒有 email、姓名、租用戶、token 或任何細節 |
| 🪶 **輕量** | 只依賴 `requests`、`PyYAML`、`PyNaCl`，沒有資料庫、沒有伺服器 |

---

## 運作方式

```
   8 個候選時段            ┌───────────────────────────────────────────┐
   每班先問：今天排我嗎？──▶│ 不在名單 → 幾秒內結束，不呼叫任何 API      │
   （平日挑 2~5 班          └───────────────────────────────────────────┘
     週末挑 1~3 班）        ┌───────────────────────────────────────────┐
                     ──────▶│ ① 隨機等 0~30 分鐘                         │
                            │ ② 逐一處理每個帳號：                       │
                            │      取 access token                       │
                            │      隨機抽 8~15 個 Graph API              │
                            │      叢發式間隔（多數 <2 秒，偶爾數分鐘）  │
                            │      429／5xx 指數退避重試 2 次            │
                            │ ③ 清除本輪建立的東西（已存活數分鐘）       │
                            │ ④ 訂閱查詢（40% 機率，不是每次都做）       │
                            │ ⑤ 輪換後的 refresh token 加密回寫 Secret   │
                            │ ⑥ 詳細結果推到 Telegram                    │
                            │ ⑦ 執行紀錄 commit 回 repo（Verified）      │
                            └───────────────────────────────────────────┘

   Telegram /test ─┐
   Actions 手動 ───┼──▶  測試模式：該帳號全部 API、零延遲、逐項回報
   API dispatch ───┘
```

每天的行程由「**當地日期 + repo 名稱**」推導 —— 同一天的每一班都算出同一份行程，
但每天不同、每個人也不同（避免全世界跑這套工具的人在同一秒一起打微軟）。

---

## 每天實際會做什麼

以下都是模擬實測的數字，不是估計值（`tools/selftest_humanize.py` 可自行驗證）。

| | |
|---|---|
| 保活執行 | 平均 **3.06 次/天**，範圍 1~5；平日 3.45、週末 1.94 |
| **零活動的日子** | **0 天**（模擬一整年） |
| Graph API 呼叫 | 平均約 **36 次/天** |
| 其中寫入型操作 | 平均約 **5 次/天**（建檔、寄信、建草稿等，全部會清除） |
| 單次執行耗時 | 平均 5.1 分鐘 |
| 抽樣重複率 | 模擬 2 萬次抽樣，**0 次重複組合** |

**呼叫節奏分布**：56% 連續操作（≤2 秒）、36% 停下來看內容（2~60 秒）、
8% 整個離開一下（1~4 分鐘）。中位數 1.8 秒但平均 24 秒 —— 右偏，跟真人的操作曲線一致。

**權重效果**：核心 API（`dir.me`、`mail.list`、`files.drive`）約 45% 的執行會被抽到，
冷門的約 18%。確保每次都有基本活動訊號，同時保持組合多變。

---

## Secrets 一覽

`repo → Settings → Secrets and variables → Actions`

| Secret | 必填 | 說明 |
|---|:--:|---|
| `E5_ACCOUNTS` | ✅ | 帳號 JSON 陣列。**由「🔑 授權帳號」workflow 自動產生**，不用手打 |
| `TELEGRAM_BOT_TOKEN` | ✅ | 沒有的話完全不會有通知，授權也無法進行 |
| `TELEGRAM_CHAT_ID` | ✅ | 只有這個對話能下指令 |
| `GH_PAT` | ⬜ | 需 **Contents + Secrets** 寫入權 |
| `TELEGRAM_OWNER_IDS` | ⬜ | **只有把 chat id 設成群組時才需要**：你的 Telegram 使用者 ID，多個用逗號分隔 |

`GITHUB_TOKEN` 不用你建立 —— Actions 每次執行自動發一個臨時的，只在 `GH_PAT` 缺席時當備援。

**`GH_PAT` 缺少某項權限時會怎樣**：

| 缺少 | 後果 |
|---|---|
| `Contents` | commit 改由 `GITHUB_TOKEN` 建立（作者變 bot，**Verified 仍在**） |
| `Secrets` | refresh token 不會自動保存，約 90 天後要手動重新授權 |
| 整個沒設 | 上述兩者都退化，且**雲端授權無法使用**。保活與通知本身完全正常 |

---

## E5_ACCOUNTS 格式

正常情況你不需要手動編輯這個 —— 用「🔑 授權帳號」和「👥 帳號管理」workflow 就好。
格式列在這裡供參考：

```json
[
  {
    "alias": "E5-主帳號",
    "email": "admin@yourtenant.onmicrosoft.com",
    "mode": "delegated",
    "tenant": "common",
    "client_id": "11111111-2222-3333-4444-555555555555",
    "refresh_token": "0.AXoA..."
  },
  {
    "alias": "E5-備援",
    "email": "admin@backup.onmicrosoft.com",
    "mode": "app",
    "tenant": "66666666-7777-8888-9999-000000000000",
    "client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "client_secret": "abc~xxxxxxxxxxxxxxxxxxxxxxxxxxx",
    "target_user": "admin@backup.onmicrosoft.com",
    "enabled": true
  }
]
```

| 欄位 | 必填 | 說明 |
|---|:--:|---|
| `alias` | ⬜ | 通知裡顯示的名字，也是指令用的識別 |
| `email` | ✅ | 帳號 UPN |
| `mode` | ⬜ | `delegated`（預設）或 `app` |
| `tenant` | ⬜ | 預設 `common`；`app` 模式必填實際租用戶 |
| `client_id` | ⬜ | 不填會用 Azure PowerShell 的公用 ID（僅建議測試） |
| `client_secret` | 視情況 | `app` 模式必填 |
| `refresh_token` | 視情況 | `delegated` 模式必填 |
| `target_user` | ⬜ | `app` 模式要操作的使用者，預設同 `email` |
| `scopes` | ⬜ | 自訂權限範圍 |
| `enabled` | ⬜ | 設 `false` 暫停該帳號（或用 `/disable`） |

> 💡 **為什麼帳號管理不能手動改 Secret**：GitHub Secret 是寫得進去、讀不回來的。
> 你在網頁上打開 `E5_ACCOUNTS` 看不到內容，想改其中一個帳號就得整包重寫 ——
> 而 refresh token 讀不出來，等於所有帳號都要重新授權。
> 指令與 workflow 是由程式讀出解密後的清單、改完再寫回去，所以能單獨處理一個帳號。

---

## Telegram 指令

**執行**

| 指令 | 作用 |
|---|---|
| `/test all` | 跑**全部**帳號的**全部** API |
| `/test <帳號>` | 只跑指定帳號的全部 API |
| `/test <帳號> mail` | 只測該帳號的某一類 API |
| `/check all` | 只驗證 token 與權限，不實際呼叫（dry-run） |
| `/run` | 立刻執行一次正常保活（隨機抽 API） |

**帳號管理**

| 指令 | 作用 |
|---|---|
| `/list` | 列出所有帳號與啟用狀態 |
| `/disable <帳號>` | 暫停該帳號的保活，但保留 token |
| `/enable <帳號>` | 恢復保活 |
| `/rename <舊別名> <新別名>` | 改顯示名稱 |
| `/remove <帳號> confirm` | 移除帳號（**不可復原**，需加 `confirm`） |

**查詢**

| 指令 | 作用 |
|---|---|
| `/status` | 看最後一次執行結果 |
| `/report` | 立刻產生統計報告 |
| `/ping` | 確認精靈還活著 |
| `/help` | 指令說明 |

帳號可用**別名、email 或清單序號**指定。

> 🔒 只有 `TELEGRAM_CHAT_ID` 指定的對話**且**寄件者本人能下指令，其他一律忽略，
> 而且 bot 從不回覆未授權對話（外人得不到任何回應或錯誤訊息）。
>
> ⏱️ 指令由每 5 分鐘一次的輪詢收取。GitHub 排程是 best-effort，
> 實際延遲通常在 5 分鐘內，**尖峰時段可能 15~30 分鐘**。急的話直接用 Actions 頁面手動跑。
> 收到 `/test`、`/run` 時會告訴你這條指令等了多久，讓你分得出「排隊中」和「沒收到」。

---

## 測試模式

**跑該帳號的全部 API、不抽樣、不加延遲、每一項都回報狀態碼與錯誤原因。**

### ① Actions 手動執行

`Actions` → **🧪 E5 測試模式** → **Run workflow**

| 欄位 | 說明 |
|---|---|
| 帳號 | 別名 / email / 序號，或 `all` |
| 類別 | `mail` / `files` / `calendar` / `directory`，或不限 |
| 只驗證 token 與權限 | dry-run，完全不呼叫 API |

### ② Telegram

```
/test all
/test E5-主帳號
/test E5-主帳號 mail
```

### ③ repository_dispatch

```bash
curl -X POST https://api.github.com/repos/<你的帳號>/<repo>/dispatches \
  -H "Authorization: Bearer <你的 PAT>" \
  -H "Accept: application/vnd.github+json" \
  -d '{"event_type":"e5-test","client_payload":{"account":"all","category":"","dry_run":false}}'
```

---

## config.yml 參數

改完 commit 就生效，不用動程式碼。

### 擬人化

| 參數 | 預設 | 說明 |
|---|---|---|
| `humanize.enabled` | `true` | 總開關，設 `false` 完全退回機械式行為 |
| `humanize.slots` | 8 個時段 | 候選時段（UTC，**必須和 keepalive.yml 的 cron 一致**） |
| `humanize.runs_per_day` | `[2, 5]` | 平日從候選裡挑幾個來跑 |
| `humanize.weekend_runs_per_day` | `[1, 3]` | 週末挑幾個 |
| `humanize.burst_probability` | `0.6` | 連續操作的機率（其餘為停下來看內容） |
| `humanize.burst_delay` | `[0.3, 2]` | 連續操作的間隔（秒） |
| `humanize.pause_delay` | `[5, 60]` | 停下來看內容（秒） |
| `humanize.long_pause_probability` | `0.08` | 整個離開一下的機率 |
| `humanize.long_pause_delay` | `[60, 240]` | 離開多久（秒） |
| `humanize.deferred_cleanup` | `true` | 建立的東西留到本輪結束才刪 |
| `humanize.subscription_check_probability` | `0.4` | 訂閱查詢的機率 |
| `humanize.repeat_probability` | `0.12` | 偶爾重複開同一個東西 |

### 執行

| 參數 | 預設 | 說明 |
|---|---|---|
| `timezone` | `Asia/Taipei` | 通知與紀錄顯示的時區 |
| `schedule.random_delay_max_minutes` | `30` | 被選中的班次再隨機等多久才開始 |
| `run.min_apis` / `run.max_apis` | `8` / `15` | 每次隨機抽幾個 API |
| `run.api_delay_seconds` | `[1, 8]` | 擬人化關閉時的間隔；設 `[0, 0]` 完全不等待 |
| `run.account_delay_seconds` | `[5, 45]` | 每個帳號之間隨機停幾秒 |
| `run.retry.max_attempts` | `3` | 初次 1 次 + 重試 2 次 |
| `run.retry.initial_backoff` | `2` | 第一次重試前等幾秒 |
| `run.retry.multiplier` | `2` | 之後每次退避時間乘以幾倍 |
| `run.timeout_seconds` | `30` | 單次 HTTP 請求逾時 |
| `api_categories` | 四類全開 | 想關掉某一類直接註解 |

### 功能

| 參數 | 預設 | 說明 |
|---|---|---|
| `features.write_operations` | `true` | 是否建立／刪除測試檔案、草稿、行事曆 |
| `features.send_self_mail` | `true` | 是否寄信給自己 |
| `features.cleanup_after_write` | `true` | 寫入後是否清除 |
| `features.subscription_reminder` | `true` | 訂閱到期倒數提醒 |
| `features.reminder_days` | `[30,14,7,3,1]` | 提醒門檻 |
| `features.history` | `true` | 執行紀錄寫回 repo |
| `features.history_detail` | `minimal` | `minimal` 只寫別名與成功率；`full` 額外寫 email、訂閱天數、SKU |
| `features.weekly_report` | `true` | 每週統計 |

### 通知

| 參數 | 預設 | 說明 |
|---|---|---|
| `notify.detail` | `full` | `full` = 逐項明細，`summary` = 只有統計 |
| `notify.mask_email` | `true` | 通知裡遮蔽 email |
| `notify.max_detail_lines` | `60` | 排程通知最多列幾行（測試模式不受限） |
| `notify.silent_success` | `false` | `true` = 全部成功時不發通知 |

---

## Actions 額度與成本

**GitHub Actions 對公開 repo 完全免費不限量，對私有 repo 每月只有 2000 分鐘。**
而且每個 job 最少計費 1 分鐘 —— 跑 10 秒也算 1 分鐘。

| 元件 | 預設設定 | 每月約略用量 |
|---|---|---|
| 🛡️ 保活執行 | 8 次 cron/天，其中約 3 次實際執行 | ~2 000 分鐘 |
| 🤖 Telegram 輪詢 | 每 5 分鐘 | ~8 600 分鐘 |
| 📈 每週統計 | 每週 1 次 | ~5 分鐘 |
| | | **合計 ~10 700 分鐘** |

### 公開 repo（建議）

不用調整，維持預設即可。金鑰在 Secrets 裡是加密的，公開也讀不到；
執行紀錄與日誌已經做過清洗（見[安全性](#安全性)）。

### 私有 repo

預設會嚴重超標。**兩個調整就能壓進免費額度**：

1. `config.yml` → `schedule.random_delay_max_minutes` 從 `30` 改成 `5`
   （保活降到約 **860 分鐘/月**）
2. `.github/workflows/telegram-poller.yml` → cron 改成 `'0 * * * *'`（每小時）
   （降到約 **720 分鐘/月**）

合計約 **1 580 分鐘**，在 2000 額度內。

> 想更省就直接停用輪詢 workflow（`Actions` → 選它 → `⋯` → `Disable workflow`），
> 只剩約 **865 分鐘/月**。代價是 Telegram 指令不能用，
> 但 Actions 手動執行與 `repository_dispatch` 兩種觸發方式仍然可用。
>
> ⚠️ 每 30 分鐘的輪詢（`*/30`）約需 1 440 分鐘，加上保活會超標，不建議。

---

## 安全性

這個專案假設你會把 repo 設成**公開**，所以防護是照著這個前提設計的。
公開 repo 有兩個所有人都讀得到的地方，而且很容易被忽略：

> **① Actions 執行日誌** —— 而且是**即時串流**的，不用等執行結束就看得到。
> **② commit 進 repo 的檔案** —— 而且是**永久**的，被 fork 過就刪不掉了。

### 誰能執行你的 Actions？

**只有你。** 所有 workflow 只用三種觸發器，每一種都需要 repo 寫入權限：

| 觸發器 | 誰能觸發 |
|---|---|
| `workflow_dispatch` | 需要寫入權限，按鈕對沒權限的人不顯示 |
| `repository_dispatch` | 需要有寫入權的 token，否則回 404 |
| `schedule` | GitHub 自己，沒有人參與 |

關鍵是**完全沒有用到 `pull_request`、`issue_comment`、`workflow_run`** ——
那些才是外人碰得到的觸發器。陌生人可以 fork、開 issue、送 PR，
但你的 repo 裡不會有任何 workflow 因此執行。

有人 fork 後在**他自己的** fork 裡執行，用的是他的額度、拿不到你的 Secret
（fork 不繼承 Secret），而且 GitHub 預設會停用 fork 的排程 workflow。

> 順手確認一下 `Settings` → `Collaborators and teams` 裡只有你 ——
> 有寫入權的協作者等同於能執行所有 workflow。

### 憑證

- 金鑰只存在 GitHub Secrets，加密儲存，repo 公開也讀不到
- refresh token、client secret、access token **一行都不會被印出來**。
  每個衍生值都在第一次可能被輸出之前就先送出 `::add-mask::`
  （GitHub 只會自動遮蔽 `E5_ACCOUNTS` 這一整包，裡面個別的 token 它並不認得）
- 回寫 Secret 用 libsodium sealed box 加密，跟 GitHub 網頁介面同一套機制
- 所有 GitHub token 都放在 `Authorization` 標頭而非網址，連線錯誤訊息不會夾帶它
- `git push` 備援自備 credential helper 從環境變數讀 token，
  不依賴 `actions/checkout` 的內部做法，token 也不會出現在指令列

### 授權流程

- **裝置碼只送到 Telegram，不進日誌。** 它不是密碼，但任何人都能拿去
  用**自己的**微軟帳號兌換 —— 那會搶走你的授權，並把對方的帳號寫進你的設定
- 可在授權時填「預期帳號」，登入的不是那個人就中止且不寫入任何東西
- 沒有 `GH_PAT` 就直接中止，而不是退而求其次把 token 印出來

### 公開日誌與公開檔案的內容

- Graph 的錯誤訊息會原封不動回吐你的完整 email，AADSTS 會夾帶租用戶 ID ——
  寫進 `history/`、`STATUS.md`、Job Summary 之前一律替換成 `<email>` / `<id>`
- API 回傳摘要（姓名、組織名、信件數、OneDrive 用量、主管姓名）
  **只送 Telegram 私訊，不進 Actions 日誌**
- Telegram 觸發紀錄裡的使用者名稱會在 commit 前移除
- 預設不把 email（連遮蔽版都不寫，網域會洩漏租用戶）、訂閱天數、SKU 寫進紀錄

### Telegram

- 兩道獨立檢查：對話要對，**寄件者也要對**。
  chat id 設成群組時必須另外設 `TELEGRAM_OWNER_IDS`，否則一律拒絕（fail closed）
- 陌生人的訊息**不會觸發任何 commit** —— 否則知道 bot 名稱的人每 5 分鐘傳一則，
  就能在你的公開 repo 灌進整天的 commit，而且掛在你名下
- bot 從不主動回覆非授權對話

### 供應鏈

- 所有 GitHub Actions 用 **commit SHA 釘死**（tag 可以被改指向，SHA 不行），
  並使用 Node 24 的 v6 版本（v4/v5 的 Node 20 遲早會被 GitHub 停用）
- `requirements.txt` 鎖定完整相依樹的確切版本。輪詢每 5 分鐘就跑一次
  `pip install`，而那個 job 手上有你所有的 token —— 版本浮動等於把風險敞開
- 附 `.github/dependabot.yml`，更新照樣自動收到 PR

### 為什麼不做程式碼混淆

攻擊者要的東西全在**輸出**裡，不在原始碼裡：日誌是他讀的、commit 進去的檔案是他讀的。
而且 `.github/workflows/*.yml` **無法混淆**（GitHub 必須讀得懂才能執行），
觸發方式、權限、secret 接線本來就完全公開。

混淆反而會拿掉最重要的一項保障：上面「憑證」那段的遮蔽順序是個**順序相依的隱性約定**
（`gh_add_mask` 必須先於第一次輸出）。程式可讀時四個呼叫點看一眼就能驗證；
混淆之後你自己也驗證不了，而下一次隨手調換兩行的重構就會變成無聲的憑證外洩。
同時 CodeQL、Dependabot、secret scanning 全部失效 —— 那些才是會替你擋下下一個漏洞的東西。

<details>
<summary><b>repo 公開，別人到底看得到什麼？</b></summary>

**看得到**：原始碼、workflow 設定、`config.yml`、所有 Actions 執行日誌（不用登入）、
以及 `history/` 裡的執行紀錄 —— 預設只有帳號別名、成功／失敗數、
失敗的 API 名稱與狀態碼、token 輪換的時間點。

**看不到**：任何 token、client secret、email（連遮蔽版都不寫）、訂閱剩餘天數與 SKU、
你的姓名、組織名、信箱內容、OneDrive 用量、登入代碼、
Telegram bot token / chat id / 使用者名稱。

想連 token 輪換時間點都不公開，把 `features.history` 設成 `false`。
代價是 repo 會沒有新 commit，**GitHub 會在 60 天後自動停用排程** ——
屆時要偶爾手動推一個 commit 或手動執行一次 workflow。

反過來，私有 repo 想留完整趨勢的話把 `history_detail` 改成 `full`。
</details>

執行 `python tools/selftest_privacy.py` 可以離線驗證上述所有防護是否仍然成立。

---

## 常見問題

<details>
<summary><b>Actions 裡好多「幾秒就結束」的執行，是壞了嗎？</b></summary>

**正常的。** 保活 workflow 每天有 8 個候選時段被 cron 觸發，但每天只會實際執行其中
2~5 班（週末 1~3 班）。沒被選中的班次會在幾秒內結束，日誌裡會寫
「今天排 N 次…這班不在名單內」。這是刻意的行為變異，不是故障。
</details>

<details>
<summary><b>為什麼有些 API 顯示 ⚠️ 403 / 404？</b></summary>

這是**預期內的**，不算失敗：

- `403` → 那個 API 需要你沒授與的權限（例如報表類需要系統管理員角色）
- `404` → 資料本來就不存在（例如你沒設大頭貼、沒有主管）

只有 ❌ 才需要處理。要讓 ⚠️ 消失就到 Azure 補上對應權限；不補完全不影響保活。
</details>

<details>
<summary><b>Telegram 一直沒收到通知</b></summary>

1. 有沒有**先對 bot 傳過訊息**？沒有的話 bot 不能主動私訊你
2. `TELEGRAM_CHAT_ID` 是不是負數（群組 id 是負的，那需要額外設 `TELEGRAM_OWNER_IDS`）
3. Actions 日誌裡有 `getUpdates 回應 HTTP 401` → bot token 貼錯
4. 有 `chat not found` → chat id 貼錯

跑一次 **🧪 E5 測試模式** 看日誌最清楚。
</details>

<details>
<summary><b>提示 <code>invalid_grant</code> 怎麼辦？</b></summary>

Refresh token 失效了（超過 90 天沒用、密碼變更、或被撤銷）。
重跑一次「🔑 授權帳號」workflow 即可，其他帳號不受影響。

設定了有 `Secrets` 權限的 `GH_PAT` 的話，token 會自動續命，不太會遇到這個問題。
</details>

<details>
<summary><b>commit 沒有 Verified 標記</b></summary>

代表 `GH_PAT` 和 `GITHUB_TOKEN` **兩個都**沒能建立簽章 commit，退回了 `git push`。
日誌裡會有 `改用一般 git push 備援`，往上找就是原因，通常是 PAT 少了 `Contents` 權限。

如果有 Verified 但**作者是 `github-actions[bot]` 而不是你**，代表 PAT 這關沒過、
由 `GITHUB_TOKEN` 接手了 —— 補上 PAT 的 `Contents` 權限即可。
</details>

<details>
<summary><b>不想讓保活 commit 洗版我的貢獻圖</b></summary>

把 `GH_PAT` 的 **Contents** 權限拿掉（保留 `Secrets`）。commit 會自動落到
`GITHUB_TOKEN`，作者變成 `github-actions[bot]`、**一樣有 Verified**、不進貢獻圖，
而 refresh token 照樣自動回寫。
</details>

<details>
<summary><b>排程突然停了</b></summary>

GitHub 會在 repo **連續 60 天沒有任何 commit** 時自動停用排程 workflow。
本專案預設會把執行紀錄 commit 回 repo，所以不會遇到 —— 除非你關掉了 `features.history`。
</details>

<details>
<summary><b>這樣真的能讓 E5 續訂嗎？</b></summary>

這個工具做的是**持續產生真實的 Microsoft Graph API 活動訊號**，
並讓這些訊號在時間分布與行為節奏上盡量接近真人使用。

但要說清楚：**續訂與否的最終決定權在 Microsoft，其判準從未公開**，
過去改過好幾次、未來也可能再改。這個工具**不保證**續訂成功，
它只是把該做的活動訊號做好做滿。請當成提高機率的手段，不是保證。
</details>

<details>
<summary><b>想自己加 API</b></summary>

打開 `e5keeper/apis.py`，照著 `CATALOG` 裡的格式加一筆：

```python
ApiSpec("mail.myapi", "mail", "我的 API",
        "{u}/messages?$top=3",
        tolerate=(403,), summarize=s_count("封"), weight=2),
```

- `{u}` 會自動換成 `/me`（委派）或 `/users/xxx`（應用程式）
- `tolerate` 裡的狀態碼顯示 ⚠️ 而不是 ❌
- `weight` 越大越常被隨機抽到

改完跑 `python tools/selftest.py` 就能離線確認沒改壞。
</details>

---

## 檔案結構

```
e5-keeper/
├── .github/
│   ├── dependabot.yml        # 自動追蹤相依套件與 Actions 更新
│   └── workflows/
│       ├── authorize.yml     # 🔑 雲端授權，不需要本機環境
│       ├── accounts.yml      # 👥 帳號管理（列出／停用／移除／改名）
│       ├── keepalive.yml     # 🛡️ 8 個候選時段，每天實際跑 2~5 次
│       ├── test-run.yml      # 🧪 測試模式（手動 / repository_dispatch）
│       ├── telegram-poller.yml # 🤖 每 5 分鐘收 Telegram 指令
│       └── weekly-report.yml # 📈 每週統計報告
├── e5keeper/
│   ├── main.py               # 指令列進入點
│   ├── config.py             # 設定與帳號載入
│   ├── humanize.py           # 擬人化：每日行程與呼叫節奏
│   ├── auth.py               # 取得 access token（雙模式）
│   ├── device_auth.py        # 雲端裝置碼授權
│   ├── accounts.py           # 帳號管理
│   ├── apis.py               # Graph API 目錄（52 個端點）★ 想加 API 改這裡
│   ├── graph.py              # HTTP 用戶端 + 重試 + 結果整理
│   ├── runner.py             # 執行引擎（抽樣、節奏、延後清除）
│   ├── notify.py             # Telegram 通知與排版
│   ├── ghsecrets.py          # 加密回寫 GitHub Secrets
│   ├── gitapi.py             # 建立 GitHub 簽章的 Verified commit
│   ├── history.py            # 歷史紀錄 + 提交（簽章優先、git push 備援）
│   ├── report.py             # 每週統計
│   ├── telegram_poll.py      # 指令輪詢與派發
│   └── utils.py              # 遮蔽、清洗、時間、日誌
├── tools/
│   ├── get_token.py          # 本機授權工具（雲端授權的替代方案）
│   ├── selftest.py           # 離線模擬：保活流程與通知
│   ├── selftest_commit.py    # 離線模擬：Verified commit 與併發衝突
│   ├── selftest_privacy.py   # 離線模擬：公開場所的外洩防護
│   ├── selftest_reliability.py # 離線模擬：指令通道可靠性
│   └── selftest_humanize.py  # 離線模擬：擬人化行為統計
├── config.yml                # ★ 行為參數都在這
├── requirements.txt          # 完整鎖定版本（含間接相依）
├── 安裝教學.md               # ★ 第一次安裝看這個
├── CHANGELOG.md              # 版本之間改了什麼、為什麼
├── STATUS.md                 # 自動產生，別手動改
└── history/                  # 自動產生的執行紀錄
```

---

## 開發與測試

五套離線測試，**完全不碰真實帳號、不需要網路**（會在本機起假的 Microsoft／Telegram／GitHub 伺服器）：

```bash
python tools/selftest.py             # 保活流程、通知排版、邊界情況
python tools/selftest_commit.py      # Verified commit、併發衝突、備援路徑
python tools/selftest_privacy.py     # 公開日誌與公開檔案的外洩防護
python tools/selftest_reliability.py # 指令遺失、重試、降級路徑
python tools/selftest_humanize.py    # 擬人化行為的統計性質
```

本機執行主程式：

```bash
pip install -r requirements.txt
export E5_ACCOUNTS='[{...}]'
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_CHAT_ID='...'

python -m e5keeper validate            # 檢查設定與 PAT 權限，不呼叫 Graph API
python -m e5keeper test --account all  # 測試模式
python -m e5keeper run                 # 排程模式（本機不會 commit）
python -m e5keeper accounts --action list
```

`validate` 會實際打 GitHub API 確認 PAT 的 Contents 與 Secrets 兩項權限，
在 Actions 裡跑最準（本機沒有 `GITHUB_REPOSITORY` 會跳過線上檢查）。

---

## 免責聲明

本工具僅對**你自己擁有的** Microsoft 365 租用戶呼叫官方 Microsoft Graph API，
不涉及任何破解、繞過或濫用行為。是否續訂 E5 開發者訂閱由 Microsoft 決定，
其判準從未公開，本工具不作任何保證。使用前請確認符合你所在地區與 Microsoft 的服務條款。

---

<p align="center"><sub>用 🐍 Python 寫成 · 只依賴 3 個套件 · 沒有伺服器、沒有資料庫</sub></p>
