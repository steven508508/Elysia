"""Telegram 指令輪詢。

GitHub 沒有常駐服務，所以用一個每 5 分鐘跑一次的 workflow 去 getUpdates，
收到指令就「就地執行」（不再去 dispatch 另一個 workflow，少一層依賴、也不需要
額外的 PAT 權限）。

安全性：只接受設定檔裡那個 TELEGRAM_CHAT_ID 送來的訊息，其他一律忽略。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import requests

from . import history, notify
from .config import Settings
from .notify import DIVIDER, e, send_text
from .runner import run_all
from .utils import fmt_time, log, mask_email, now_local, section

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "state"
OFFSET_FILE = STATE_DIR / "telegram_offset.json"

HELP = """🤖 <b>E5 保活精靈 · 指令說明</b>

<b>執行</b>
<code>/test all</code>　跑<b>全部</b>帳號的<b>全部</b> API（測試模式）
<code>/test 主帳號</code>　只跑指定帳號的全部 API
<code>/test 主帳號 mail</code>　只測該帳號的郵件類 API
<code>/check all</code>　只驗證 token 與權限，不實際呼叫
<code>/run</code>　立刻執行一次正常保活（隨機抽 API）

<b>帳號管理</b>
<code>/list</code>　列出所有帳號與啟用狀態
<code>/disable 主帳號</code>　暫停保活，但保留 token
<code>/enable 主帳號</code>　恢復保活
<code>/rename 舊別名 新別名</code>　改顯示名稱
<code>/remove 主帳號 confirm</code>　移除帳號（<b>不可復原</b>）

<b>查詢</b>
<code>/status</code>　看最後一次執行結果
<code>/report</code>　立刻產生一份統計報告
<code>/ping</code>　確認精靈還活著
<code>/help</code>　顯示這則說明

<i>帳號可以用別名、email 或清單上的序號指定。
指令最多 5 分鐘內會被處理（輪詢間隔）。</i>"""


# ══════════════════ offset 狀態 ══════════════════

def _load_offset() -> int:
    try:
        return int(json.loads(OFFSET_FILE.read_text(encoding="utf-8")).get("offset", 0))
    except Exception:  # noqa: BLE001
        return 0


def _allowed_senders(settings: Settings) -> set[str]:
    """哪些 Telegram 使用者 ID 可以下指令。

    私人對話：chat.id 就等於對方的 user id，所以 TELEGRAM_CHAT_ID 本身就是答案。
    群組：chat.id 是負數，跟成員的 user id 無關 —— 這種情況必須另外指定
          TELEGRAM_OWNER_IDS，否則一律拒絕（fail closed，寧可不能用也不放行）。
    """
    extra = {
        x.strip()
        for x in os.environ.get("TELEGRAM_OWNER_IDS", "").replace(";", ",").split(",")
        if x.strip()
    }
    chat = str(settings.telegram_chat_id).strip()
    if chat and not chat.startswith("-"):
        extra.add(chat)
    return extra


def _save_offset(value: int) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(
        json.dumps({"offset": value}, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# ══════════════════ 主流程 ══════════════════

def poll(settings: Settings) -> int:
    if not settings.telegram_token or not settings.telegram_chat_id:
        log("未設定 Telegram，無法輪詢指令", level="warn")
        return 0

    offset = _load_offset()
    url = f"{notify.API_ROOT}/bot{settings.telegram_token}/getUpdates"
    params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if offset:
        params["offset"] = offset + 1

    try:
        resp = requests.get(url, params=params, timeout=30)
        # ⚠️ 不要把 exception 直接格式化進日誌 —— requests 的錯誤訊息會夾帶完整
        #    請求網址，而 bot token 就在網址裡。GitHub 雖然會自動遮蔽已註冊的
        #    secret，但那層保護一旦遇到編碼或拆分就失效，不能當成唯一防線。
        if not resp.ok:
            log(f"getUpdates 回應 HTTP {resp.status_code}"
                + ("（bot token 不正確？）" if resp.status_code == 401 else ""),
                level="err")
            return 0
        data = resp.json()
    except requests.RequestException as exc:
        log(f"getUpdates 連線失敗：{exc.__class__.__name__}", level="err")
        return 0
    except ValueError:
        log("getUpdates 回傳的不是 JSON", level="err")
        return 0

    updates = data.get("result") or []
    if not updates:
        log("沒有新指令")
        return 0

    log(f"收到 {len(updates)} 筆更新")
    highest = offset
    commands: list[tuple[str, list[str], str]] = []
    rejected = 0
    allowed_senders = _allowed_senders(settings)

    for upd in updates:
        highest = max(highest, int(upd.get("update_id", 0)))
        msg = upd.get("message") or {}
        chat_id = str((msg.get("chat") or {}).get("id", ""))
        sender_id = str((msg.get("from") or {}).get("id", ""))
        text = (msg.get("text") or "").strip()
        sender = (msg.get("from") or {}).get("username") or sender_id or "?"

        # 兩道獨立的檢查：對話要對，「送訊息的人」也要對。
        # 只檢查 chat_id 的話，一旦 TELEGRAM_CHAT_ID 設成群組，
        # 群裡任何人都能下 /run、/test —— 群組若可被搜尋或有邀請連結，那就是任何人。
        if chat_id != str(settings.telegram_chat_id):
            rejected += 1
            log("忽略來自未授權對話的訊息", level="warn")
            continue
        if sender_id not in allowed_senders:
            rejected += 1
            log(f"忽略未授權寄件者（id={sender_id}）的訊息", level="warn")
            if not allowed_senders:
                log("TELEGRAM_CHAT_ID 看起來是群組。請另外設定 TELEGRAM_OWNER_IDS "
                    "（你的 Telegram 使用者 ID，多個用逗號分隔），否則所有指令都會被拒絕。",
                    level="warn")
            continue
        if not text.startswith("/"):
            continue

        parts = text.split()
        cmd = parts[0].lstrip("/").split("@")[0].lower()
        commands.append((cmd, parts[1:], sender))

    if not commands:
        # 只有陌生人來訊時，絕對不能提交 —— 否則任何知道 bot 名稱的人
        # 每 5 分鐘傳一則訊息，就能在你的公開 repo 灌進一整天的 commit，
        # 而且因為優先使用 GH_PAT，那些 commit 會掛在你名下。
        # 位移只存在本機，下一輪重讀舊值最多就是再忽略一次，沒有副作用。
        _save_offset(highest)
        log(f"沒有需要處理的指令" + (f"（忽略了 {rejected} 則未授權訊息）" if rejected else ""))
        return 0

    # 有真正要執行的指令才寫回 offset，避免執行失敗時下次重跑同一條
    _save_offset(highest)
    if not history.commit_and_push(
        "chore(e5keeper): 更新 Telegram 指令位移",
        ["state"],
        rewrite=lambda: _save_offset(highest),
    ):
        log("位移沒能存回 repo，下一輪可能會重跑同一條指令", level="warn")

    for cmd, argv, sender in commands:
        log(f"執行指令 /{cmd} {' '.join(argv)}（來自 {sender}）")
        try:
            _dispatch(settings, cmd, argv, sender)
        except Exception as exc:  # noqa: BLE001
            log(f"指令執行失敗：{exc}", level="err")
            send_text(settings, f"❌ 指令 <code>/{e(cmd)}</code> 執行失敗\n"
                                f"<code>{e(f'{exc.__class__.__name__}: {exc}')}</code>")
    return 0


def _dispatch(settings: Settings, cmd: str, argv: list[str], sender: str) -> None:
    if cmd in ("help", "start"):
        send_text(settings, HELP)
        return

    if cmd == "ping":
        send_text(settings, f"🏓 pong　<i>{e(fmt_time(now_local(settings.tz)))}</i>")
        return

    if cmd in ("list", "enable", "disable", "remove", "rename"):
        from . import accounts as acct

        # /rename 舊別名 新別名　／　/remove 別名 confirm
        target = argv[0] if argv else ""
        new_alias = argv[1] if (cmd == "rename" and len(argv) > 1) else ""
        confirm = any(a.lower() in ("confirm", "確定", "yes") for a in argv[1:])
        _, message = acct.apply_action(settings, cmd, target, new_alias, confirm)
        send_text(settings, message)
        return

    if cmd == "status":
        send_text(settings, _last_status(settings))
        return

    if cmd == "report":
        from . import report as report_mod

        report_mod.weekly(settings, force=True)   # 手動要的一定要給
        return

    if cmd in ("test", "check", "run"):
        _run_command(settings, cmd, argv, sender)
        return

    send_text(settings, f"❓ 不認識的指令 <code>/{e(cmd)}</code>\n\n{HELP}")


def _run_command(settings: Settings, cmd: str, argv: list[str], sender: str) -> None:
    from . import accounts as acct

    key = (argv[0] if argv else "all").strip()
    category = argv[1].lower() if len(argv) > 1 else ""
    valid_cats = set(settings.categories)
    if category and category not in valid_cats:
        send_text(settings, f"❓ 沒有 <code>{e(category)}</code> 這個類別，"
                            f"可用：{e('、'.join(sorted(valid_cats)))}")
        return

    if cmd == "run":
        targets = [a for a in settings.accounts if a.enabled]
        test_mode = False
        dry = False
        who = f"全部 {len(targets)} 個帳號"
    else:
        test_mode = True
        dry = (cmd == "check")
        if key.lower() in ("all", "全部", "*"):
            targets = [a for a in settings.accounts if a.enabled]
            who = f"全部 {len(targets)} 個帳號"
        else:
            acc = settings.find_account(key)
            if acc is None:
                send_text(settings, f"❌ 找不到帳號 <code>{e(key)}</code>\n\n"
                                    + acct.render_list(settings, "可用的帳號"))
                return
            targets = [acc]
            who = acc.alias

    if not targets:
        send_text(settings, "⚠️ 沒有啟用中的帳號")
        return

    banner = {
        "test": f"🧪 <b>收到測試指令</b>\n目標：<b>{e(who)}</b>",
        "check": f"🔍 <b>收到檢查指令</b>（dry-run）\n目標：<b>{e(who)}</b>",
        "run": f"▶️ <b>收到立即執行指令</b>\n目標：<b>{e(who)}</b>",
    }[cmd]
    if category:
        banner += f"\n類別：<code>{e(category)}</code>"
    banner += f"\n請稍候，跑完會把完整結果送過來…"
    send_text(settings, banner)

    section(f"Telegram 指令 /{cmd} · {who}")
    rep = run_all(
        settings,
        test_mode=test_mode,
        accounts=targets,
        trigger=f"telegram:/{cmd} ({sender})",
        only_category=category,
        dry_run=dry,
    )

    from .main import _finish

    _finish(settings, rep)


# ══════════════════ 查詢類指令 ══════════════════

def _last_status(settings: Settings) -> str:
    entries = history.load_entries(days=30)
    if not entries:
        return "ℹ️ 目前還沒有任何執行紀錄。"
    last = entries[-1]
    accs = last.get("accounts") or []
    ok = sum(a.get("ok", 0) for a in accs)
    warn = sum(a.get("warn", 0) for a in accs)
    fail = sum(a.get("fail", 0) for a in accs)
    icon = "✅" if fail == 0 and all(a.get("token_ok") for a in accs) else "❌"

    rows = []
    for a in accs:
        mark = "✅" if a.get("token_ok") and not a.get("fail") else \
               ("🚫" if not a.get("token_ok") else "❌")
        days = a.get("days_left")
        tail = f"  訂閱剩 {days} 天" if days is not None else ""
        rows.append(f"{mark} {a.get('alias', '?')}  "
                    f"{a.get('ok', 0)}✅ {a.get('warn', 0)}⚠️ {a.get('fail', 0)}❌{tail}")

    return "\n".join([
        f"{icon} <b>最後一次執行</b>",
        f"🕐 {e(last.get('ts', '?'))} UTC　模式 <code>{e(last.get('mode', '?'))}</code>",
        f"🔀 觸發：<code>{e(last.get('trigger', '?'))}</code>",
        DIVIDER,
        f"📊 {ok} ✅　{warn} ⚠️　{fail} ❌",
        "<pre>" + e("\n".join(rows)) + "</pre>",
        f"<i>共有 {len(entries)} 筆近 30 天紀錄</i>",
    ])
