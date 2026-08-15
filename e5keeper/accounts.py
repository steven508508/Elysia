"""帳號管理：列出、停用、啟用、移除、改名。

為什麼需要這個模組：GitHub Secret 是**寫得進去、讀不回來**的。
帳號資料一旦存進 E5_ACCOUNTS，你在網頁上就再也看不到內容，
自然也沒辦法「只改其中一個帳號」——想刪掉一個就得整包重寫，
等於所有帳號都要重新授權。

但程式執行時手上有解密後的完整清單，所以由它來改再寫回去，
就能做到單一帳號層級的增刪改。
"""

from __future__ import annotations

from .config import Account, Settings
from .ghsecrets import save_accounts
from .notify import e
from .utils import log, mask_email

ACTIONS = ("list", "enable", "disable", "remove", "rename")


class AccountError(Exception):
    pass


# ══════════════════ 列出 ══════════════════

def render_list(settings: Settings, title: str = "帳號清單") -> str:
    accounts = settings.accounts
    if not accounts:
        return ("👥 <b>目前沒有任何帳號</b>\n\n"
                "用「🔑 授權帳號」workflow 新增一個。")

    mask = settings.notify.get("mask_email", True)
    on = sum(1 for a in accounts if a.enabled)
    rows = []
    for i, a in enumerate(accounts, 1):
        flag = "🟢" if a.enabled else "⚪"
        mode = "委派" if a.mode == "delegated" else "應用程式"
        tail = "" if a.enabled else "　<i>（已停用）</i>"
        rows.append(f"{flag} <b>{i}. {e(a.alias)}</b>{tail}")
        rows.append(f"　　 <code>{e(mask_email(a.email, mask))}</code> · {mode}")

    return "\n".join([
        f"👥 <b>{e(title)}</b>　共 {len(accounts)} 個，{on} 個啟用中",
        "",
        *rows,
        "",
        "<i>用 /help 看可用的管理指令</i>",
    ])


# ══════════════════ 操作 ══════════════════

def apply_action(
    settings: Settings,
    action: str,
    target: str = "",
    new_alias: str = "",
    confirm: bool = False,
) -> tuple[bool, str]:
    """執行一項帳號管理操作，回傳 (是否有變更, 要回報的 HTML 訊息)。"""
    action = (action or "").strip().lower()
    if action not in ACTIONS:
        return False, (f"❓ 不認識的操作 <code>{e(action)}</code>\n"
                       f"可用：{'、'.join(ACTIONS)}")

    if action == "list":
        return False, render_list(settings)

    if not target:
        return False, "❓ 請指定要操作哪個帳號（別名、email 或序號）"

    account = settings.find_account(target)
    if account is None:
        return False, (f"❌ 找不到帳號 <code>{e(target)}</code>\n\n"
                       + render_list(settings, "可用的帳號"))

    accounts = list(settings.accounts)
    mask = settings.notify.get("mask_email", True)
    who = f"<b>{e(account.alias)}</b>　<code>{e(mask_email(account.email, mask))}</code>"

    # ── 停用 / 啟用 ──
    if action in ("disable", "enable"):
        want = (action == "enable")
        if account.enabled == want:
            state = "啟用中" if want else "停用中"
            return False, f"ℹ️ {who}\n已經是{state}了，沒有變更。"

        if not want and sum(1 for a in accounts if a.enabled) == 1:
            if not confirm:
                return False, (
                    f"⚠️ {who}\n"
                    f"這是<b>唯一啟用中</b>的帳號，停用之後保活就完全停擺了。\n\n"
                    f"確定的話請加上 confirm：\n"
                    f"<code>/disable {e(target)} confirm</code>"
                )
        account.enabled = want
        verb = "已啟用" if want else "已停用"
        note = ("\n\n▶️ 下一次排程就會開始保活它。" if want else
                "\n\n⏸️ 它會被跳過，但 token 仍保留著，隨時可以 /enable 開回來。")

    # ── 改名 ──
    elif action == "rename":
        new_alias = (new_alias or "").strip()
        if not new_alias:
            return False, "❓ 請指定新的別名"
        if len(new_alias) > 40:
            return False, "❓ 別名太長了（上限 40 字）"
        clash = next((a for a in accounts
                      if a is not account and a.alias.lower() == new_alias.lower()), None)
        if clash is not None:
            return False, f"❌ 別名 <code>{e(new_alias)}</code> 已經被用了"
        old = account.alias
        account.alias = new_alias
        who = f"<b>{e(old)}</b> → <b>{e(new_alias)}</b>"
        verb = "已改名"
        note = ""

    # ── 移除 ──
    else:
        if not confirm:
            return False, (
                f"⚠️ <b>確定要移除這個帳號嗎？</b>\n{who}\n\n"
                f"移除後它的 refresh token 會被一併刪掉，"
                f"想加回來必須<b>重新跑一次授權</b>。\n\n"
                f"確定的話請加上 confirm：\n"
                f"<code>/remove {e(target)} confirm</code>"
            )
        accounts = [a for a in accounts if a is not account]
        verb = "已移除"
        note = ("\n\n⚠️ <b>目前一個帳號都不剩了</b>，保活會完全停擺。"
                if not accounts else "")

    # ── 寫回 Secret ──
    if not settings.can_write_secrets:
        return False, ("❌ <b>無法儲存變更</b>\n"
                       "缺少 <code>GH_PAT</code>，或它沒有 Secrets 的寫入權限。\n"
                       "請先補上再操作，否則改動不會生效。")

    ok, msg = save_accounts(settings, accounts)
    if not ok:
        return False, (f"❌ <b>寫入 Secret 失敗</b>\n<code>{e(msg)}</code>\n\n"
                       f"這次的變更<b>沒有生效</b>。")

    settings.accounts = accounts
    log(f"{verb}：{account.alias}", level="ok")

    return True, "\n".join([
        f"✅ <b>{verb}</b>",
        who + note,
        "",
        render_list(settings, "更新後的帳號清單"),
        "",
        "<i>ℹ️ GitHub 的 Secret 要下一次執行才會讀到新內容，這是它的設計。</i>",
    ])
