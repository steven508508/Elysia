# 🛡️ E5 保活精靈

> 專為 GitHub Actions 打造的輕量版 Microsoft 365 E5 開發者訂閱保活工具。
> 每次執行都把「哪個帳號、在跑哪個 API、結果如何」完整推到你的 Telegram，
> 也可以隨時下指令強制立刻跑完某個帳號的全部 API。

<p align="center">
  <img alt="Python 3.9+" src="https://img.shields.io/badge/Python-3.9%2B-3776AB?logo=python&logoColor=white">
  <img alt="GitHub Actions" src="https://img.shields.io/badge/GitHub_Actions-2088FF?logo=githubactions&logoColor=white">
  <img alt="Telegram" src="https://img.shields.io/badge/Telegram-26A5E4?logo=telegram&logoColor=white">
  <img alt="相依套件 3 個" src="https://img.shields.io/badge/相依套件-3%20個-success">
  <img alt="version 1.5.0" src="https://img.shields.io/badge/version-1.5.0-blue">
</p>

---

## 目錄

- [特色](#特色)
- [運作方式](#運作方式)
- [部署教學](#部署教學)
- [Secrets 一覽](#secrets-一覽)
- [E5_ACCOUNTS 格式](#e5_accounts-格式)
- [測試模式](#測試模式)
- [Telegram 指令](#telegram-指令)
- [config.yml 參數](#configyml-參數)
- [Actions 額度與成本](#actions-額度與成本)
- [常見問題](#常見問題)
- [檔案結構](#檔案結構)
- [安全性](#安全性)

新手請先看 **[安裝教學.md](安裝教學.md)**，版本差異看 **[CHANGELOG.md](CHANGELOG.md)**。

---

## 特色

| | |
|---|---|
| 📨 **每次執行都詳細回報** | 哪個帳號、跑了哪些 API、HTTP 狀態碼、耗時、回傳摘要，逐項列給你看 |
| 🧪 **測試模式** | 一句指令就強制立刻跑完指定帳號的**全部** API，不抽樣、不延遲、逐項回報 |
| 👥 **多帳號** | 全部放在一個 JSON Secret 裡，串行執行、帳號間隨機延遲 |
| 🔑 **雙認證模式** | 委派權限（refresh token）與應用程式權限（client credentials）都支援，可混用 |
| 🔄 **Token 自動續命** | Microsoft 輪換 refresh token 後，自動加密回寫 GitHub Secret，不用手動維護 |
| 🎲 **行為隨機化** | 每次隨機抽 8–15 個 API、隨機順序、隨機間隔、每天執行時刻也隨機 |
| 👥 **帳號管理** | 停用／啟用／改名／移除單一帳號，不用重新授權其他帳號 |
| ⏳ **到期倒數提醒** | 自動抓訂閱剩餘天數，30／14／7／3／1 天時重點提醒 |
| 📈 **每週統計** | 成功率、最常失敗的 API、token 輪換次數，每週一自動推送 |
| 📝 **歷史紀錄** | 每次執行寫回 repo，順帶讓 GitHub 不會因為「60 天沒動靜」停用排程 |
| ✅ **Verified commit** | 產生的 commit 由 GitHub 自動簽章、顯示 Verified，不用管任何 GPG 金鑰 |
| 🧯 **失效不擴散** | 某個帳號 token 掛了 → 通知你並附上修復步驟，其他帳號照跑 |
| 🔒 **隱私** | 通知裡的帳號一律遮蔽成 `ab***@xx.com`，日誌裡的 token 自動打碼 |
| 🪶 **輕量** | 只依賴 `requests`、`PyYAML`、`PyNaCl`，沒有資料庫、沒有伺服器 |

---

## 運作方式

```
                    ┌──────────────────────────────────────────┐
   每天 3 次排程 ──▶ │  ① 隨機延遲 0~50 分鐘（每天時刻都不同）  │
   （UTC 01:23      │  ② 逐一處理每個帳號：                     │
     09:47 17:11）  │       取 access token                     │
                    │       隨機抽 8~15 個 Graph API 呼叫       │
                    │       429／5xx 自動指數退避重試 2 次      │
                    │       查訂閱剩餘天數                      │
                    │  ③ 輪換後的 refresh token 加密回寫 Secret │
                    │  ④ 詳細結果推到 Telegram                 │
                    │  ⑤ 執行紀錄 commit 回 repo               │
                    └──────────────────────────────────────────┘

   Telegram /test ─┐
   Actions 手動 ───┼──▶  測試模式：該帳號全部 API、零延遲、逐項回報
   API dispatch ───┘
```

---

## 部署教學

> 全程大約 15 分鐘。**建議把 repo 設為公開**（Actions 免費不限額度；金鑰放在 Secrets 裡，
> 就算 repo 公開也不會外洩）。要用私有 repo 也行，請先看 [Actions 額度與成本](#actions-額度與成本)。

### 步驟 1 · 建立你的 repo

把這個專案的檔案放進一個新的 GitHub repo（fork、上傳 zip、或直接 push 都可以）。

建好之後開啟兩個開關：

1. **Actions → 啟用**
   如果是 fork 來的，進 `Actions` 分頁點一下 *"I understand my workflows, go ahead and enable them"*。
2. **允許 Actions 寫入 repo**
   `Settings → Actions → General → Workflow permissions` → 選 **Read and write permissions** → Save。
   （執行紀錄要 commit 回 repo，沒開這個會推不上去。）

---

### 步驟 2 · 在 Azure 註冊一個應用程式

到 [portal.azure.com](https://portal.azure.com) 用你的 **E5 系統管理員帳號**登入。

1. 搜尋並進入 **Microsoft Entra ID**（舊名 Azure AD）→ 左側 **應用程式註冊** → **新增註冊**
2. 填寫：
   - **名稱**：隨便取，例如 `E5 Keeper`
   - **支援的帳戶類型**：選「**任何組織目錄中的帳戶和個人 Microsoft 帳戶**」
   - **重新導向 URI**：留空即可（我們用裝置碼流程，不需要）
   - 按 **註冊**
3. 註冊完成後，複製 **應用程式 (用戶端) 識別碼** — 這就是待會要用的 `client_id`

4. 左側 **驗證** → 拉到最下面 **進階設定**
   → **允許公用用戶端流程** 選 **是** → **儲存**
   > ⚠️ 這步一定要做，否則裝置碼流程會失敗。

5. 左側 **API 權限** → **新增權限** → **Microsoft Graph** → **委派的權限**，勾選：

   ```
   offline_access      openid              profile
   User.Read           User.ReadBasic.All  Mail.ReadWrite
   Mail.Send           MailboxSettings.Read
   Files.ReadWrite.All Calendars.ReadWrite Contacts.ReadWrite
   Tasks.ReadWrite     Notes.Read          People.Read
   Sites.Read.All      Team.ReadBasic.All  Directory.Read.All
   ```

   加完之後按 **代表 <你的組織> 授與管理員同意** → 是。

   > 少勾幾個不會壞掉，對應的 API 只會顯示 ⚠️ 403 而已。

---

### 步驟 3 · 建立 Telegram Bot

1. 在 Telegram 搜尋 **[@BotFather](https://t.me/BotFather)** → 傳 `/newbot`
2. 依指示取名，完成後會拿到一串 token，長得像
   `7123456789:AAF-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
   → 這是 `TELEGRAM_BOT_TOKEN`
3. **對你剛建立的 bot 傳任何一句話**（例如 `hi`）—— 這步不能省，
   Telegram 規定 bot 不能主動私訊沒跟它說過話的人。
4. 取得你的 chat id：在瀏覽器打開
   `https://api.telegram.org/bot<你的TOKEN>/getUpdates`
   找到 `"chat":{"id":123456789` 這個數字 → 這是 `TELEGRAM_CHAT_ID`
   （或直接找 **[@userinfobot](https://t.me/userinfobot)** 傳 `/start`，它會告訴你）

---

> 💡 **請用「和 bot 的一對一私人對話」，不要用群組。** 群組的 chat id 是負數，
> 那種情況必須另外設定 `TELEGRAM_OWNER_IDS`，否則所有指令都會被拒絕
> （這是刻意的：只檢查群組 id 的話，群裡任何人都能對你的帳號下指令）。

---

### 步驟 4 · 建立 GitHub PAT

`Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token`

- **Repository access**：Only select repositories → 選你的 repo
- **Repository permissions**：這**兩項**都要設成 **Read and write**

  | 權限 | 用途 |
  |---|---|
  | **Contents** | 建立 ✅ Verified commit（用你的身分） |
  | **Secrets** | 輪換後的 refresh token 自動回寫 |

- 產生後複製那串 `github_pat_...` → 這是 `GH_PAT`

> 用傳統 token 也可以，勾 `repo` 這個 scope 就同時涵蓋兩者。
>
> **少了 `Contents`** → commit 自動退回一般 git push，沒有 Verified 標記，但紀錄照樣留得住。
> **少了 `Secrets`** → refresh token 不會自動保存，大約 90 天後要手動重新授權一次。
> **完全不設 `GH_PAT`** → 兩者都退化，但保活與通知本身完全正常。

---

### 步驟 5 · 先填 3 個 Secret

`repo → Settings → Secrets and variables → Actions → New repository secret`：

| 名稱 | 內容 |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 步驟 3 的 bot token |
| `TELEGRAM_CHAT_ID` | 步驟 3 的 chat id |
| `GH_PAT` | 步驟 4 的 PAT |

`E5_ACCOUNTS` 先不用管 —— 下一步會自動幫你建好。

---

### 步驟 6 · 授權你的 E5 帳號

這一步會產生 `E5_ACCOUNTS`。有兩條路，**不需要本機環境的那條在下面**。

<details open>
<summary><b>方式 A（推薦）· 在 GitHub 上完成，不用碰自己的電腦</b></summary>

前提：步驟 5 的三個 Secret 已經填好了。

1. `Actions` → 左側選 **🔑 授權帳號（不需要本機環境）** → **Run workflow**
2. 填三個欄位：
   - **帳號別名**：例如 `E5-主帳號`
   - **用戶端 ID**：步驟 2 複製的那串
   - **預期登入的帳號**：填你的 E5 帳號 email。**強烈建議填** ——
     萬一代碼被別人搶去兌換，這裡會擋下來並中止，不會污染你的設定
   - 租用戶保持 `common`
3. 十幾秒後 Telegram 會收到：

   ```
   🔑 E5 保活精靈 · 帳號授權
   1️⃣ 開啟 👉 https://microsoft.com/devicelogin
   2️⃣ 輸入代碼 👉 KQPW-3M2X
   3️⃣ 用你要保活的 E5 帳號登入並同意授權
   ```

4. 照著做完，Telegram 會回報 `✅ 已新增帳號`，`E5_ACCOUNTS` 就自動建好了

要加第二個帳號就再跑一次，填不同的別名即可。同一個 email 會被覆寫（可關閉）。

> 🔒 **refresh token 和裝置碼都不會出現在 Actions 日誌裡。**
> 公開 repo 的執行日誌是所有人都看得到的、而且是即時串流的 —— 代碼要是印在那裡，
> 有人盯著就能搶先用**他自己的**微軟帳號兌換掉。所以代碼只送你的 Telegram，
> token 則直接加密寫進 Secret。也因此這個 workflow 一定要有 `GH_PAT`，
> 沒有的話會直接中止，而不是退而求其次把 token 印出來。

</details>

<details>
<summary><b>方式 B · 在本機執行（需要 Python 3.9+）</b></summary>

在**你自己的電腦**上（不是 GitHub 上）執行：

```bash
git clone <你的 repo 網址>
cd e5-keeper
pip install requests
python tools/get_token.py
```

工具會問你幾個問題（租用戶按 Enter 用 `common` 就好），然後顯示：

```
  1. 開啟網址：https://microsoft.com/devicelogin
  2. 輸入代碼：K7QW9M2XZ
  3. 用你的 E5 帳號 登入並同意授權
```

照著做完，工具會自動驗證權限並印出一段 JSON —— **這段就是待會要貼的 `E5_ACCOUNTS`**。

**多個帳號**就重複執行，用 `--append` 把它們併在一起：

```bash
python tools/get_token.py --alias "E5-主帳號"
python tools/get_token.py --alias "E5-備援" --append accounts.json
```

<details>
<summary>其他用法</summary>

```bash
# 已經有 refresh token，只想驗證它還活著
python tools/get_token.py --paste

# 建立應用程式權限（client_credentials）的帳號設定
python tools/get_token.py --app
```

應用程式權限模式需要在 Azure 加**應用程式權限**（不是委派權限）並授與管理員同意，
且 `tenant` 必須填實際的租用戶 ID 或網域，不能用 `common`。
它的好處是不會過期，缺點是活動訊號屬於「背景服務」而非「使用者本人」。
建議**主帳號用委派、備援用應用程式**，兩者混用最穩。
</details>

> ⚠️ `accounts.json` 含有金鑰，貼進 Secret 後請刪掉。`.gitignore` 已經幫你擋住了。

</details>

---

### 步驟 7 · 驗收

到 `Actions` 分頁 → 左側選 **🧪 E5 測試模式** → **Run workflow**
→ 帳號填 `all` → 綠色按鈕。

一兩分鐘後 Telegram 應該會收到完整的逐項報告。收到就代表全部設定完成了 🎉

想先確認設定有沒有填錯（完全不呼叫 API），可以把 **只驗證 token 與權限** 勾起來跑一次。

---

## Secrets 一覽

| Secret | 必填 | 說明 |
|---|:--:|---|
| `E5_ACCOUNTS` | ✅ | 帳號 JSON 陣列，見下方格式 |
| `TELEGRAM_BOT_TOKEN` | ✅ | 沒有的話完全不會有通知 |
| `TELEGRAM_CHAT_ID` | ✅ | 只有這個對話能下指令 |
| `GH_PAT` | ⬜ | 需要 Contents + Secrets 寫入權；沒有的話 Verified commit 與 token 自動回寫都會退化，雲端授權也無法使用 |
| `TELEGRAM_OWNER_IDS` | ⬜ | **只有把 `TELEGRAM_CHAT_ID` 設成群組時才需要**：你的 Telegram 使用者 ID（多個用逗號分隔）。不設定的話群組裡的所有指令都會被拒絕 |

`GITHUB_TOKEN` 不用你建立 —— Actions 每次執行會自動發一個臨時的，
只在 `GH_PAT` 缺席時當備援（那時 commit 會顯示為 `github-actions[bot]`）。

---

## E5_ACCOUNTS 格式

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
| `alias` | ⬜ | 通知裡顯示的名字，也是 `/test <別名>` 用的識別 |
| `email` | ✅ | 帳號 UPN |
| `mode` | ⬜ | `delegated`（預設）或 `app` |
| `tenant` | ⬜ | 預設 `common`；`app` 模式必填實際租用戶 |
| `client_id` | ⬜ | 不填會用 Azure PowerShell 的公用 ID（僅建議測試用） |
| `client_secret` | 視情況 | `app` 模式必填；公用用戶端的 `delegated` 不用 |
| `refresh_token` | 視情況 | `delegated` 模式必填 |
| `target_user` | ⬜ | `app` 模式要操作的使用者，預設同 `email` |
| `scopes` | ⬜ | 自訂權限範圍，不填用內建清單 |
| `enabled` | ⬜ | 設 `false` 可暫時停用某個帳號 |

---

## 測試模式

**跑該帳號的全部 API、不抽樣、不加隨機延遲、每一項都回報狀態碼與錯誤原因。**
三種觸發方式：

### ① GitHub Actions 手動執行

`Actions` → **🧪 E5 測試模式** → **Run workflow**

| 欄位 | 說明 |
|---|---|
| 帳號 | 別名 / email / 序號，或 `all` |
| 類別 | 只測某一類：`mail` / `files` / `calendar` / `directory` |
| 只驗證 token 與權限 | dry-run，只確認設定正確，完全不呼叫 API |

### ② Telegram 傳指令

```
/test all
/test E5-主帳號
/test E5-主帳號 mail
```

指令由 `telegram-poller.yml` 每 5 分鐘收一次，所以最多等 5 分鐘。

### ③ repository_dispatch API

```bash
curl -X POST https://api.github.com/repos/<你的帳號>/<repo>/dispatches \
  -H "Authorization: Bearer <你的 PAT>" \
  -H "Accept: application/vnd.github+json" \
  -d '{"event_type":"e5-test","client_payload":{"account":"all","category":"","dry_run":false}}'
```

---

## Telegram 指令

| 指令 | 作用 |
|---|---|
| `/test all` | 跑**全部**帳號的**全部** API |
| `/test <帳號>` | 跑指定帳號的全部 API |
| `/test <帳號> mail` | 只測該帳號的郵件類 API |
| `/check all` | 只驗證 token 與權限，不實際呼叫（dry-run） |
| `/run` | 立刻執行一次正常保活（隨機抽 API） |
| `/status` | 查看最後一次執行結果 |
| `/list` | 列出所有帳號與啟用狀態 |
| `/disable <帳號>` | 暫停該帳號的保活，但保留 token |
| `/enable <帳號>` | 恢復保活 |
| `/rename <舊別名> <新別名>` | 改顯示名稱 |
| `/remove <帳號> confirm` | 移除帳號（**不可復原**，需加 confirm） |
| `/report` | 立刻產生一份統計報告 |
| `/help` | 指令說明 |
| `/ping` | 確認精靈還活著 |

> 🔒 只有 `TELEGRAM_CHAT_ID` 指定的那個對話能下指令，其他人傳的一律忽略。
> 如果要放在群組裡用，記得到 BotFather 把 **Group Privacy** 關掉，bot 才收得到指令。

---

## config.yml 參數

改完 commit 就生效，不用動程式碼。

| 參數 | 預設 | 說明 |
|---|---|---|
| `timezone` | `Asia/Taipei` | 通知與紀錄顯示的時區 |
| `schedule.random_delay_max_minutes` | `50` | 排程觸發後再隨機等多久才開始 |
| `run.min_apis` / `max_apis` | `8` / `15` | 每次排程隨機抽幾個 API |
| `run.api_delay_seconds` | `[1, 8]` | 每個 API 之間隨機停幾秒 |
| `run.account_delay_seconds` | `[5, 45]` | 每個帳號之間隨機停幾秒 |
| `run.retry.max_attempts` | `3` | 初次 1 次 + 重試 2 次 |
| `features.write_operations` | `true` | 是否建立／刪除測試檔案、草稿、行事曆 |
| `features.send_self_mail` | `true` | 是否寄信給自己 |
| `features.subscription_reminder` | `true` | 訂閱到期倒數提醒 |
| `features.history` | `true` | 執行紀錄寫回 repo |
| `features.history_detail` | `minimal` | 寫進 repo 的紀錄詳細度。`minimal` 只寫別名與成功率；`full` 額外寫 email、訂閱天數、SKU |
| `notify.detail` | `full` | `full` = 逐項明細，`summary` = 只有統計 |
| `notify.mask_email` | `true` | 通知裡遮蔽 email |
| `notify.max_detail_lines` | `60` | 排程通知最多列幾行（測試模式不受限） |
| `api_categories` | 四類全開 | 想關掉某一類直接註解 |

---

## Actions 額度與成本

**這一節請務必看完。**

GitHub Actions 對**公開 repo 完全免費不限量**，對**私有 repo 每月只有 2000 分鐘**。
而且 GitHub 每個 job 最少計費 1 分鐘 —— 跑 10 秒也算 1 分鐘。

| 元件 | 預設設定 | 每月約略用量 |
|---|---|---|
| 🛡️ 保活執行 | 每天 3 次 × 隨機等待平均 25 分 | ~2 300 分鐘 |
| 🤖 Telegram 輪詢 | 每 5 分鐘 | ~8 600 分鐘 |
| 📈 每週統計 | 每週 1 次 | ~5 分鐘 |

### 公開 repo（建議）

不用做任何調整，維持預設即可。金鑰放在 Secrets 裡是加密的，
repo 公開也不會外洩 —— 只有你的執行紀錄和設定檔會被看到。

### 私有 repo

預設設定會嚴重超標，請做兩件事：

1. **`config.yml`** → 把 `schedule.random_delay_max_minutes` 從 `50` 改成 `5`
   （每月降到約 250 分鐘）
2. **`.github/workflows/telegram-poller.yml`** → 把 cron 改成 `'*/30 * * * *'`
   （每月降到約 1 440 分鐘），或直接停用這個 workflow —— 停用之後
   Telegram 指令就不能用了，但 Actions 手動執行與 `repository_dispatch` 兩種
   測試模式觸發方式仍然可用。

調整後總用量約 1 700 分鐘，剛好在免費額度內。

---

## Verified commit

精靈寫回 repo 的執行紀錄，在 GitHub 上都會帶 ✅ **Verified** 標記，
而且**不需要你產生、保管任何 GPG 或 SSH 金鑰**。

原理是不用 `git push`，改呼叫 GitHub 的 GraphQL mutation `createCommitOnBranch`。
GitHub 官方文件對這個 mutation 的說明是：

> Commits made using this mutation are automatically signed by GitHub
> if supported and will be marked as verified in the user interface.

簽章由 GitHub 在伺服器端蓋，所以私鑰從頭到尾不存在，也就沒有外洩的可能。

**commit 會顯示成誰？** 取決於用哪個 token：

| Token | commit 作者 | 計入貢獻圖 | 何時使用 |
|---|---|:--:|---|
| `GH_PAT` | **你本人** | ✅ | 有設定 PAT 時的預設 |
| `GITHUB_TOKEN` | `github-actions[bot]` | ❌ | 沒設 PAT、或 PAT 缺 Contents 權限時自動接手 |

兩個 token 會依序嘗試，所以 PAT 權限不夠時 Verified 標記仍然保得住，只是作者變成 bot。

目前的設定是**用你自己的帳號**，所以每天 3 次保活會在你的貢獻圖上留下綠格子。
不想要的話，把 `GH_PAT` 從 Secrets 移除即可（代價是 refresh token 不再自動回寫）。

**併發安全**：多個 workflow 可能同時要提交（排程剛好碰上 Telegram 輪詢）。
`createCommitOnBranch` 需要帶上 `expectedHeadOid`，對不上就會被 GitHub 拒絕 ——
精靈收到拒絕後會重新同步遠端、把這次的變更重新套上去再送一次，
所以**不會蓋掉別人剛寫進去的紀錄**。`tools/selftest_commit.py` 就是在驗證這件事。

**退化路徑**：PAT 少了 `Contents` 權限、或 GitHub API 異常時，會自動退回一般 `git push`，
紀錄照樣留得住，只是那幾筆 commit 不會有 Verified 標記。

---

## 常見問題

<details>
<summary><b>排程時間怎麼算的？為什麼要隨機？</b></summary>

基準 cron 是 UTC `01:23` / `09:47` / `17:11`（台北時間 `09:23` / `17:47` / `01:11`），
相隔 8 小時。程式啟動後再隨機等 0～50 分鐘，所以每天實際執行時刻都不一樣，
且兩次執行至少相隔 7 小時，符合你要的「間隔不少於一小時」。

刻意避開整點，是因為每天整點是 GitHub 排程最壅塞的時候，容易被延遲十幾分鐘。
順帶一提，GitHub 的排程本來就是「盡力而為」，偶爾晚個幾分鐘是正常的。
</details>

<details>
<summary><b>為什麼有些 API 顯示 ⚠️ 403 / 404？</b></summary>

這是**預期內的**，不算失敗：

- `403` → 那個 API 需要你沒授與的權限（例如報表類需要系統管理員權限）
- `404` → 資料本來就不存在（例如你沒設大頭貼、沒有主管）

只有 ❌ 才是真的需要處理。要讓 ⚠️ 消失，就到 Azure 補上對應權限；
不補也完全不影響保活效果。
</details>

<details>
<summary><b>Telegram 一直沒收到通知</b></summary>

依序檢查：

1. 有沒有**先對 bot 傳過訊息**？沒有的話 bot 不能主動私訊你
2. `TELEGRAM_CHAT_ID` 是不是負數（群組的 id 是負的，要連負號一起填）
3. Actions 執行日誌裡有沒有 `Telegram 回應 401` → token 貼錯
4. 有沒有 `Telegram 回應 400: chat not found` → chat id 貼錯

想快速測，跑一次 **🧪 E5 測試模式** 看 Actions 日誌最清楚。
</details>

<details>
<summary><b>提示 <code>invalid_grant</code> 怎麼辦？</b></summary>

Refresh token 失效了（超過 90 天沒用、密碼變更、或被撤銷）。
在本機重跑 `python tools/get_token.py`，把新的 JSON 更新到 `E5_ACCOUNTS` Secret 就好。

設定了 `GH_PAT` 的話，正常情況下 token 會自動續命，不太會遇到這個問題。
</details>

<details>
<summary><b>執行紀錄推不回 repo</b></summary>

先看 Actions 日誌裡是哪一條路徑失敗的：

- `token 權限不足…需要 Contents 的讀寫權限` → PAT 少了 **Contents: Read and write**
- 退回 git push 後又失敗 → `Settings → Actions → General → Workflow permissions`
  要選 **Read and write permissions**

多個 workflow 同時提交造成的衝突會自動處理（重新同步後重試 3 次），
真的失敗也只是少一筆紀錄，不影響保活本身。
</details>

<details>
<summary><b>commit 沒有出現 Verified 標記</b></summary>

代表 `GH_PAT` 和 `GITHUB_TOKEN` **兩個都**沒能建立簽章 commit，才會退回 git push。
日誌裡會有一行 `改用一般 git push 備援（commit 不會有 Verified 標記）`，往上找就是原因：

1. **兩個 token 都沒有 Contents 權限** → 檢查 PAT 設定，以及
   `Settings → Actions → General → Workflow permissions` 有沒有選 Read and write
2. **`GH_PAT` 過期了，且 workflow 沒傳 `GITHUB_TOKEN`** → 重新產生 PAT，
   或確認 workflow 的 `env:` 有 `GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}`
3. **GitHub API 當時異常** → 下次執行就會恢復，不用處理

跑 `python -m e5keeper validate` 可以直接看出是哪一項權限不夠。

如果 commit 有 Verified 但**作者是 `github-actions[bot]` 而不是你**，
代表 PAT 這一關沒過、由 `GITHUB_TOKEN` 接手了 —— 補上 PAT 的 Contents 權限即可。
</details>

<details>
<summary><b>不想讓保活 commit 洗版我的貢獻圖</b></summary>

把 `GH_PAT` 這個 Secret 刪掉，精靈會自動改用 Actions 內建的 `GITHUB_TOKEN`，
commit 作者變成 `github-actions[bot]`，**一樣有 Verified 標記**，但不計入你的貢獻圖。

代價是 refresh token 不再自動回寫，大約 90 天後要手動重新授權一次。

**兩者兼得的做法**：`GH_PAT` 保留，但只給它 **Secrets** 權限、**不要**給 Contents。
這樣 refresh token 照樣自動回寫，而 commit 會因為 PAT 沒有 Contents 權限
自動落到 `GITHUB_TOKEN` —— 作者是 bot、仍然 Verified、不進貢獻圖。
</details>

<details>
<summary><b>排程突然停了</b></summary>

GitHub 會在 repo **連續 60 天沒有任何 commit** 時自動停用排程 workflow。
本專案預設會把執行紀錄 commit 回 repo，所以不會遇到這個問題 ——
除非你把 `features.history` 關掉了。關掉的話請記得偶爾手動推一個 commit。
</details>

<details>
<summary><b>這樣真的能讓 E5 續訂嗎？</b></summary>

這個工具做的是**持續產生真實的 Microsoft Graph API 活動訊號**，
這是目前公認會被 Microsoft 納入「開發者是否仍在使用」評估的行為之一。

但要說清楚：**續訂與否的最終決定權在 Microsoft**，
其政策過去改過好幾次、未來也可能再改。這個工具**不保證**一定續訂成功，
它只是把該做的活動訊號做好做滿。請把它當成提高機率的手段，不是保證。
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

<details>
<summary><b>本機怎麼跑 / 怎麼除錯</b></summary>

```bash
pip install -r requirements.txt
export E5_ACCOUNTS='[{...}]'
export TELEGRAM_BOT_TOKEN='...'
export TELEGRAM_CHAT_ID='...'

python -m e5keeper validate            # 只檢查設定與 PAT 權限，不呼叫 Graph API
python -m e5keeper test --account all  # 測試模式
python -m e5keeper run                 # 排程模式（本機不會 commit）

python tools/selftest.py               # 離線模擬：保活流程、通知排版、邊界情況
python tools/selftest_commit.py        # 離線模擬：Verified commit、併發衝突、備援路徑
python tools/selftest_privacy.py       # 離線模擬：公開日誌與公開檔案的外洩防護
```

`validate` 會實際打 GitHub API 確認 PAT 的 Contents 與 Secrets 兩項權限夠不夠，
在 Actions 裡跑最準（本機沒有 `GITHUB_REPOSITORY` 會跳過線上檢查）。
</details>

---

## 檔案結構

```
e5-keeper/
├── .github/workflows/
│   ├── authorize.yml         # 🔑 雲端授權，不需要本機環境
│   ├── accounts.yml          # 👥 帳號管理（列出／停用／移除／改名）
│   ├── keepalive.yml         # 🛡️ 每天 3 次保活（cron + 隨機延遲）
│   ├── test-run.yml          # 🧪 測試模式（手動 / repository_dispatch）
│   ├── telegram-poller.yml   # 🤖 每 5 分鐘收 Telegram 指令
│   └── weekly-report.yml     # 📈 每週統計報告
├── e5keeper/
│   ├── main.py               # 指令列進入點
│   ├── config.py             # 設定與帳號載入
│   ├── accounts.py           # 帳號管理
│   ├── auth.py               # 取得 access token（雙模式）
│   ├── device_auth.py        # 雲端裝置碼授權
│   ├── apis.py               # Graph API 目錄（52 個端點）★ 想加 API 改這裡
│   ├── graph.py              # HTTP 用戶端 + 重試 + 結果整理
│   ├── runner.py             # 執行引擎（抽樣、隨機化、訂閱偵測）
│   ├── notify.py             # Telegram 通知與排版
│   ├── ghsecrets.py          # 加密回寫 GitHub Secrets
│   ├── gitapi.py             # 建立 GitHub 簽章的 Verified commit
│   ├── history.py            # 歷史紀錄 + 提交（簽章優先、git push 備援）
│   ├── report.py             # 每週統計
│   └── telegram_poll.py      # 指令輪詢與派發
├── tools/
│   ├── get_token.py          # 裝置碼授權工具 ★ 在本機執行
│   ├── selftest.py           # 離線模擬：保活流程與通知
│   ├── selftest_commit.py    # 離線模擬：Verified commit 與併發衝突
│   └── selftest_privacy.py   # 離線模擬：公開場所的外洩防護
├── .github/dependabot.yml    # 自動追蹤相依套件與 Actions 更新
├── config.yml                # ★ 行為參數都在這
├── requirements.txt          # 完整鎖定版本（含間接相依）
├── STATUS.md                 # 自動產生，別手動改
└── history/                  # 自動產生的執行紀錄
```

---

## 安全性

這個專案假設你會把 repo 設成**公開**，所以防護是照著這個前提設計的。
公開 repo 有兩個所有人都讀得到的地方，而且很容易被忽略：

> **① Actions 執行日誌** —— 而且是**即時串流**的，不用等執行結束就看得到。
> **② commit 進 repo 的檔案** —— 而且是**永久**的，被 fork 過就刪不掉了。

真正的攻擊面在這兩個地方，不在原始碼。以下是對應的處理：

**憑證**

- 金鑰只存在 GitHub Secrets，加密儲存，repo 公開也讀不到
- refresh token、client secret、access token **一行都不會被印出來**。
  每個衍生值都在第一次可能被輸出之前就先送出 `::add-mask::`
  （GitHub 只會自動遮蔽 `E5_ACCOUNTS` 這一整包，裡面個別的 token 它並不認得）
- 回寫 Secret 用 libsodium sealed box 加密，跟 GitHub 網頁介面同一套機制
- 所有 GitHub token 都放在 `Authorization` 標頭而非網址，所以連線錯誤訊息不會夾帶它

**授權流程**

- **裝置碼只送到你的 Telegram，不進日誌。** 它不是密碼，但任何人都能拿去
  用**自己的**微軟帳號兌換 —— 那會搶走你的授權，並把對方的帳號寫進你的設定
- 可在授權時填「預期帳號」，登入的不是那個人就中止且不寫入任何東西
- 沒有 `GH_PAT` 就直接中止，而不是退而求其次把 token 印出來

**公開日誌與公開檔案的內容**

- Graph 的錯誤訊息會原封不動回吐你的完整 email，AADSTS 會夾帶租用戶 ID ——
  寫進 `history/`、`STATUS.md`、Job Summary 之前一律清洗成遮蔽形式
- API 回傳摘要（你的姓名、組織名、信件數、OneDrive 用量、主管姓名）
  **只送 Telegram 私訊，不進 Actions 日誌**
- Telegram 觸發紀錄裡的使用者名稱會在 commit 前移除

**Telegram**

- 兩道獨立檢查：對話要對，**寄件者也要對**。
  `TELEGRAM_CHAT_ID` 設成群組時，必須另外設 `TELEGRAM_OWNER_IDS`，否則一律拒絕
- 陌生人的訊息**不會觸發任何 commit** —— 否則知道 bot 名稱的人每 5 分鐘傳一則，
  就能在你的公開 repo 灌進整天的 commit，而且掛在你名下
- bot 從不主動回覆非授權對話，所以外人得不到任何回應或錯誤訊息

**供應鏈**

- 所有 GitHub Actions 用 **commit SHA 釘死**（tag 可以被改指向，SHA 不行）
- `requirements.txt` 鎖定完整相依樹的確切版本。輪詢每 5 分鐘就跑一次
  `pip install`，而那個 job 手上有你所有的 token —— 版本浮動等於把這個風險敞開
- 附 `.github/dependabot.yml`，更新照樣會自動收到 PR

**其他**

- 寫入型操作全部自動清除：檔案、草稿、行事曆、聯絡人建立後立刻刪掉；
  唯一留痕的是寄給自己的保活郵件（`config.yml` 可關閉 `send_self_mail`）
- 提交只限 `history`、`STATUS.md`、`state` 三個路徑，不會誤收工作目錄裡的其他檔案
- `.gitignore` 已擋掉 `accounts.json`、`.env`、`token_output.json`

### 為什麼不做程式碼混淆

因為攻擊者要的東西全在**輸出**裡，不在原始碼裡：日誌是他讀的、commit 進去的檔案是他讀的。
而且 `.github/workflows/*.yml` **無法混淆**（GitHub 必須讀得懂才能執行），
觸發方式、權限、secret 接線本來就完全公開。

混淆反而會拿掉這個專案最重要的一項保障：上面「憑證」那段的遮蔽順序是個
**順序相依的隱性約定**（`gh_add_mask` 必須先於第一次輸出）。程式可讀時，
這件事四個呼叫點看一眼就能驗證；混淆之後你自己也驗證不了，
而下一次隨手調換兩行的重構，就會變成無聲的憑證外洩。
同時 CodeQL、Dependabot、secret scanning 全部失效 —— 那些才是會替你擋下下一個漏洞的東西。

想真正減少「公開」的代價，有效的做法是上面那幾項（控制輸出內容），
或是把 repo 改成私有並依 [Actions 額度與成本](#actions-額度與成本) 調整設定。

<details>
<summary><b>把 repo 公開，別人到底看得到什麼？</b></summary>

看得到：你的 workflow 設定、`config.yml`、以及 `history/` 裡的執行紀錄 ——
預設（`history_detail: minimal`）只有帳號別名、每次的成功／失敗數、
失敗的 API 名稱與狀態碼、token 輪換的時間點。

看不到：任何 token、client secret、email（連遮蔽版都不寫，因為網域會洩漏租用戶）、
訂閱剩餘天數與 SKU、你的姓名、組織名、信箱內容、Telegram 使用者名稱、
Telegram bot token 或 chat id。

想連 token 輪換時間點都不公開，可以把 `features.history` 設成 `false`。
代價是 repo 會沒有新 commit，**GitHub 會在 60 天後自動停用排程** ——
屆時需要你偶爾手動推一個 commit，或手動執行一次 workflow。

反過來，私有 repo 想留完整趨勢的話，把 `history_detail` 改成 `full` 即可。
</details>

執行 `python tools/selftest_privacy.py` 可以離線驗證上述所有防護是否仍然成立
（它會用一個「會外洩的假帳號」跑完整流程，再逐項檢查公開日誌與提交檔案）。

---

## 免責聲明

本工具僅對**你自己擁有的** Microsoft 365 租用戶呼叫官方 Microsoft Graph API，
不涉及任何破解、繞過或濫用行為。是否續訂 E5 開發者訂閱由 Microsoft 決定，
本工具不作任何保證。使用前請確認符合你所在地區與 Microsoft 的服務條款。

---

<p align="center"><sub>用 🐍 Python 寫成 · 只依賴 3 個套件 · 沒有伺服器、沒有資料庫</sub></p>
