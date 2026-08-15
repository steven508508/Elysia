"""在 GitHub Actions 裡完成裝置碼授權，不需要本機環境。

流程：
  1. 跟 Microsoft 要一組裝置碼
  2. 把「網址 + 代碼」推到你的 Telegram
  3. 你在手機或瀏覽器上完成登入
  4. 拿到 refresh token 後，直接加密寫進 E5_ACCOUNTS Secret

⚠️ 安全性重點（兩件事，都跟「公開 repo 的 Actions 日誌是即時公開的」有關）：

  1. refresh token 從頭到尾不會被印出來，只經過記憶體然後直接加密進 Secret。
     也因此這個功能強制需要 GH_PAT，沒有的話直接中止，而不是退而求其次印出來。

  2. **裝置碼（user_code）同樣不能進日誌。** 它不是「拿到就能登入你帳號」的東西，
     但任何人都可以拿它去 microsoft.com/devicelogin 用**自己的**微軟帳號兌換 ——
     結果是你的授權被別人搶走、而且對方的帳號會被寫進你的 E5_ACCOUNTS。
     所以代碼只送到你的私人 Telegram，日誌裡一個字都不會出現。

  另外提供 expected_upn：授權完成後會比對登入的帳號是不是你指定的那一個，
  不符就中止並且不寫入任何東西 —— 就算代碼真的被攔走也不會污染你的設定。
"""

from __future__ import annotations

import time

import requests

from .auth import LOGIN_HOST
from .config import DEFAULT_SCOPES, Account, Settings
from .ghsecrets import save_accounts
from .notify import e, send_text
from .utils import gh_add_mask, log, mask_email, section

POLL_GRACE = 5          # 比 Microsoft 給的 interval 多留幾秒，避免被 slow_down


class AuthAborted(Exception):
    pass


def authorize(
    settings: Settings,
    *,
    alias: str,
    tenant: str,
    client_id: str,
    client_secret: str = "",
    replace: bool = True,
    expected_upn: str = "",
) -> int:
    section("雲端授權：取得裝置碼")

    if not settings.can_write_secrets:
        msg = ("缺少 GH_PAT，無法把授權結果寫進 Secret。\n"
               "為了避免 token 出現在公開的 Actions 日誌裡，這個功能不提供其他儲存方式。\n"
               "請先建立 GH_PAT（需要 Secrets 的讀寫權限）再重跑一次。")
        log(msg, level="err")
        send_text(settings, f"❌ <b>授權中止</b>\n\n{e(msg)}")
        return 2

    scope = " ".join(DEFAULT_SCOPES)
    try:
        resp = requests.post(
            f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/devicecode",
            data={"client_id": client_id, "scope": scope},
            timeout=30,
        )
    except requests.RequestException as exc:
        log(f"連線 Microsoft 失敗：{exc}", level="err")
        return 1

    if not resp.ok:
        payload = _json(resp)
        code = payload.get("error", f"HTTP {resp.status_code}")
        desc = payload.get("error_description", resp.text)[:300]
        log(f"取得裝置碼失敗：{code} — {desc}", level="err")
        hint = ""
        if code in ("unauthorized_client", "invalid_client", "invalid_request"):
            hint = ("\n\n👉 最常見的原因是 Azure 應用程式沒有開啟「允許公用用戶端流程」。\n"
                    "　 Azure 入口網站 → 應用程式註冊 → 你的應用 → 驗證 →\n"
                    "　 最下方「允許公用用戶端流程」選【是】→ 儲存，再重跑一次。")
        send_text(settings, f"❌ <b>取得裝置碼失敗</b>\n<code>{e(code)}</code>\n"
                            f"<code>{e(desc)}</code>{hint}")
        return 1

    data = resp.json()
    user_code = data["user_code"]
    verify_url = data.get("verification_uri") or "https://microsoft.com/devicelogin"
    expires_in = int(data.get("expires_in", 900))
    interval = int(data.get("interval", 5))

    # 雙保險：就算之後有人不小心把它印出來，Actions 也會遮成 ***
    gh_add_mask(user_code, data.get("device_code", ""))
    log(f"裝置碼已取得（{expires_in // 60} 分鐘內有效），已送到你的 Telegram。"
        f"代碼不會出現在這份日誌裡。")

    ok = send_text(settings, "\n".join([
        "🔑 <b>E5 保活精靈 · 帳號授權</b>",
        "",
        f"1️⃣ 開啟 👉 {e(verify_url)}",
        f"2️⃣ 輸入代碼 👉 <code>{e(user_code)}</code>　<i>(點一下可複製)</i>",
        f"3️⃣ 用你要保活的 <b>E5 帳號</b>登入並同意授權",
        "",
        f"⏳ 代碼 <b>{expires_in // 60} 分鐘</b>內有效，完成後這裡會自動回報結果。",
        f"📛 這個帳號會存成：<b>{e(alias)}</b>",
        (f"🎯 只接受 <code>{e(mask_email(expected_upn))}</code> 登入，其他帳號會被拒絕。"
         if expected_upn else
         "💡 小提醒：下次可以在 workflow 填「預期帳號」，避免代碼被別人搶去兌換。"),
    ]))
    if not ok:
        # 這裡刻意不印代碼。公開 repo 的 Actions 日誌是即時公開的，
        # 印出來等於把授權機會送給任何在看的人。
        log("Telegram 送不出去，授權無法繼續。", level="err")
        log("請先確認 TELEGRAM_BOT_TOKEN／TELEGRAM_CHAT_ID 正確、"
            "而且你已經主動對 bot 傳過訊息，再重跑一次。", level="err")
        return 1

    section("等待你在瀏覽器完成授權")
    token = _poll(tenant, client_id, data["device_code"], interval, expires_in, settings)
    if token is None:
        return 1

    refresh_token = token.get("refresh_token", "")
    access_token = token.get("access_token", "")
    gh_add_mask(refresh_token, access_token)
    if not refresh_token:
        log("回應裡沒有 refresh_token（scope 少了 offline_access？）", level="err")
        send_text(settings, "❌ <b>授權失敗</b>\n沒有拿到 refresh_token，"
                            "請確認 Azure 的委派權限有勾選 <code>offline_access</code>。")
        return 1

    section("確認帳號身分")
    me = _whoami(access_token)
    upn = me.get("userPrincipalName") or me.get("mail") or ""
    display = me.get("displayName", "")
    if not upn:
        log("查不到帳號 UPN，無法建立設定", level="err")
        send_text(settings, "❌ <b>授權失敗</b>\n查不到這個帳號的 UPN，"
                            "請確認 Azure 有授與 <code>User.Read</code> 權限。")
        return 1
    log(f"授權成功：{display} <{mask_email(upn)}>", level="ok")

    # 如果指定了預期帳號，登入的必須是同一個人。
    # 這是萬一裝置碼被攔走時的最後一道防線 —— 對方用自己的帳號兌換，這裡就會擋下來。
    if expected_upn and upn.lower() != expected_upn.strip().lower():
        log(f"登入的帳號不是預期的那一個，已中止並且不寫入任何設定", level="err")
        send_text(settings, "\n".join([
            "🚨 <b>授權被拒絕：登入的不是你指定的帳號</b>",
            "",
            f"預期：<code>{e(mask_email(expected_upn))}</code>",
            f"實際：<code>{e(mask_email(upn))}</code>",
            "",
            "已中止，<b>沒有寫入任何設定</b>。",
            "如果這不是你自己操作失誤，代表剛才那組裝置碼可能被別人拿去兌換了 ——",
            "請直接重跑一次授權（舊代碼已作廢，不影響你的帳號安全）。",
        ]))
        return 1

    account = Account(
        alias=alias or f"帳號{len(settings.accounts) + 1}",
        email=upn,
        mode="delegated",
        tenant=tenant,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )

    accounts = list(settings.accounts)
    existing = next((i for i, a in enumerate(accounts) if a.email.lower() == upn.lower()), None)
    if existing is not None:
        if not replace:
            log(f"{mask_email(upn)} 已經在清單裡，且未勾選覆寫", level="err")
            send_text(settings, f"⚠️ <b>沒有寫入</b>\n"
                                f"<code>{e(mask_email(upn))}</code> 已經存在了。\n"
                                f"要更新它的話，請把「覆寫同一個帳號」勾起來再跑一次。")
            return 1
        account.alias = alias or accounts[existing].alias
        accounts[existing] = account
        action = "更新"
    else:
        accounts.append(account)
        action = "新增"

    section("寫入 E5_ACCOUNTS Secret")
    saved, msg = save_accounts(settings, accounts)
    if not saved:
        log(f"寫入失敗：{msg}", level="err")
        send_text(settings, f"❌ <b>授權成功但存檔失敗</b>\n<code>{e(msg)}</code>\n\n"
                            f"請檢查 GH_PAT 的 Secrets 權限，然後重跑一次授權。")
        return 1

    rows = "\n".join(
        f"{i}. {a.alias}　{mask_email(a.email, settings.notify.get('mask_email', True))}"
        for i, a in enumerate(accounts, 1)
    )
    send_text(settings, "\n".join([
        f"✅ <b>已{action}帳號：{e(account.alias)}</b>",
        f"🙋 {e(display)}　<code>{e(mask_email(upn))}</code>",
        "",
        f"目前 <code>E5_ACCOUNTS</code> 共有 <b>{len(accounts)}</b> 個帳號：",
        "<pre>" + e(rows) + "</pre>",
        "",
        "👉 下一步：到 Actions 跑一次「🧪 E5 測試模式」確認全部正常。",
    ]))
    log(f"已{action}帳號並寫入 Secret，目前共 {len(accounts)} 個", level="ok")
    return 0


def _poll(tenant, client_id, device_code, interval, expires_in, settings):
    deadline = time.monotonic() + expires_in
    warned_half = False
    wait = interval + POLL_GRACE

    while time.monotonic() < deadline:
        time.sleep(wait)
        remain = int(deadline - time.monotonic())

        try:
            resp = requests.post(
                f"{LOGIN_HOST}/{tenant}/oauth2/v2.0/token",
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "client_id": client_id,
                    "device_code": device_code,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            log(f"輪詢時連線失敗（會繼續重試）：{exc}", level="warn")
            continue

        if resp.ok:
            log("授權完成", level="ok")
            return resp.json()

        err = _json(resp).get("error", "")
        if err == "authorization_pending":
            log(f"等待授權中…（剩 {remain // 60} 分 {remain % 60} 秒）")
            if not warned_half and remain < expires_in // 2:
                warned_half = True
                send_text(settings, f"⏳ 代碼還剩約 <b>{remain // 60} 分鐘</b>，記得完成授權喔。")
            continue
        if err == "slow_down":
            wait += 5
            continue

        desc = _json(resp).get("error_description", "")[:300]
        log(f"授權失敗：{err} — {desc}", level="err")
        text = {
            "expired_token": "代碼已過期，請重新執行一次授權 workflow。",
            "authorization_declined": "你在瀏覽器上按了拒絕。",
            "bad_verification_code": "代碼不正確，請重新執行一次授權 workflow。",
        }.get(err, f"<code>{e(err)}</code>\n<code>{e(desc)}</code>")
        send_text(settings, f"❌ <b>授權失敗</b>\n{text}")
        return None

    log("等待逾時", level="err")
    send_text(settings, "⌛ <b>授權逾時</b>\n代碼已失效，請重新執行一次授權 workflow。")
    return None


def _whoami(access_token: str) -> dict:
    try:
        resp = requests.get(
            "https://graph.microsoft.com/v1.0/me"
            "?$select=displayName,userPrincipalName,mail,id",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        if resp.ok:
            return resp.json()
        log(f"查詢帳號資料失敗 HTTP {resp.status_code}", level="warn")
    except requests.RequestException as exc:
        log(f"查詢帳號資料失敗：{exc}", level="warn")
    return {}


def _json(resp) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {}
