"""Telegram 通知：摘要 + 逐項 API 明細。

Telegram 的 MarkdownV2 需要對十幾個字元跳脫，一個沒處理好整則訊息就送不出去；
這裡改用 HTML parse mode，再用 <pre> 等寬區塊排明細 —— 視覺效果一樣是
「Markdown 區塊 + 符號」，但穩定得多。
"""

from __future__ import annotations

import html
import os
import time

import requests

from .graph import fmt_result_line
from .runner import AccountRun, RunReport
from .utils import fmt_duration, fmt_time, log, mask_email, now_local

API_ROOT = "https://api.telegram.org"
LIMIT = 3900          # Telegram 上限 4096，留一點餘裕
DIVIDER = "━" * 22


# ══════════════════ 送出 ══════════════════

def send_text(settings, text: str, *, silent: bool = False) -> bool:
    if not settings.telegram_token or not settings.telegram_chat_id:
        log("未設定 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID，略過通知", level="warn")
        return False

    url = f"{API_ROOT}/bot{settings.telegram_token}/sendMessage"
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        "disable_notification": silent,
    }
    for attempt in range(3):
        try:
            resp = requests.post(url, json=payload, timeout=30)
        except requests.RequestException as exc:
            # 只印例外類別，不印例外內容 —— 它會夾帶含 bot token 的完整網址
            log(f"送出 Telegram 失敗（第 {attempt + 1} 次）：{exc.__class__.__name__}",
                level="warn")
            time.sleep(2 ** attempt)
            continue

        if resp.ok:
            return True
        if resp.status_code == 429:
            wait = 5
            try:
                wait = int(resp.json().get("parameters", {}).get("retry_after", 5))
            except Exception:  # noqa: BLE001
                pass
            log(f"Telegram 限流，{wait}s 後重試", level="warn")
            time.sleep(min(wait, 30))
            continue
        log(f"Telegram 回應 {resp.status_code}: {resp.text[:200]}", level="err")
        if resp.status_code == 400:
            # 多半是 HTML 沒跳脫乾淨，退回純文字再試一次
            payload.pop("parse_mode", None)
            payload["text"] = _strip_tags(text)
            continue
        return False
    return False


def send_report(settings, report: RunReport) -> None:
    if settings.notify.get("silent_success") and report.all_healthy and report.mode == "schedule":
        log("全部成功且已設定 silent_success，略過通知")
        return
    messages = build_messages(settings, report)
    sent = 0
    for msg in messages:
        if send_text(settings, msg):
            sent += 1
        time.sleep(0.4)   # 避免連續送訊息被限流

    if sent == len(messages):
        log(f"已送出 {sent} 則通知", level="ok")
    else:
        log(f"通知只送出 {sent}/{len(messages)} 則，請檢查上面的 Telegram 錯誤訊息",
            level="warn")


# ══════════════════ 組訊息 ══════════════════

def build_messages(settings, report: RunReport) -> list[str]:
    tz = settings.tz
    mask = bool(settings.notify.get("mask_email", True))
    detail_mode = settings.notify.get("detail", "full")
    max_lines = int(settings.notify.get("max_detail_lines", 60))
    if report.mode == "test":
        max_lines = 10_000        # 測試模式一定全列

    messages: list[str] = []
    header = _run_header(report, tz)
    reminder_days = settings.features.get("reminder_days")

    if not report.accounts:
        return [header + f"\n\n⚠️ {e(report.note or '沒有可執行的帳號')}"]

    # 這些東西是「之後」才黏到第一則／最後一則上的，得先把長度預留出來，
    # 否則明細塞好塞滿之後再加上去就會超過 Telegram 的 4096 字上限。
    single = len(report.accounts) == 1
    prefix_len = len(header) + len(DIVIDER) + 2
    suffix_len = len(report.secret_note) + 1 if report.secret_note else 0
    if single:
        suffix_len += len(_job_link()) + 1

    for i, run in enumerate(report.accounts):
        reserve = (prefix_len if i == 0 else 0) + (suffix_len if single else 0)
        blocks = _account_message(run, mask, detail_mode, max_lines, reminder_days, reserve)
        if i == 0:
            blocks[0] = header + "\n" + DIVIDER + "\n" + blocks[0]
        messages.extend(blocks)

    if len(report.accounts) > 1:
        messages.append(_run_footer(report, tz))
    else:
        link = _job_link()
        if link:
            messages[-1] += "\n" + link

    if report.secret_note:
        messages[-1] += "\n" + report.secret_note

    return [m for m in _enforce_limit(messages) if m.strip()]


def _run_header(report: RunReport, tz: str) -> str:
    title = "🧪 <b>E5 保活精靈 · 測試模式</b>" if report.mode == "test" \
        else "🛡️ <b>E5 保活精靈 · 排程執行</b>"
    lines = [
        title,
        f"🕐 {e(fmt_time(now_local(tz)))}　<i>{e(tz)}</i>",
        f"🔀 觸發方式：<code>{e(report.trigger)}</code>",
    ]
    if report.mode == "test":
        lines.append("⚡ 全部 API 都跑、不加隨機延遲")
    return "\n".join(lines)


def _run_footer(report: RunReport, tz: str) -> str:
    ok, warn, fail, total = report.totals
    perfect = sum(1 for a in report.accounts if a.token_ok and not a.fail_count and not a.warn_count)
    warned = sum(1 for a in report.accounts if a.token_ok and not a.fail_count and a.warn_count)
    broken = len(report.accounts) - perfect - warned
    icon = "✅" if report.all_healthy else ("❌" if (fail or broken) else "⚠️")
    lines = [
        f"{icon} <b>本輪總結</b>",
        f"👥 帳號：{perfect} ✅　{warned} ⚠️　{broken} ❌　（共 {len(report.accounts)}）",
        f"📊 API：{ok} ✅　{warn} ⚠️　{fail} ❌　（共 {total}）",
        f"⏱️ 總耗時 {e(fmt_duration(report.duration))}",
        f"🕐 {e(fmt_time(now_local(tz)))}",
    ]
    bad = [a for a in report.accounts if not a.token_ok]
    if bad:
        names = "、".join(e(a.alias) for a in bad)
        lines.append(f"🚨 需要重新授權：<b>{names}</b>")
    return "\n".join(lines) + "\n" + _job_link()


def _account_message(
    run: AccountRun, mask: bool, detail_mode: str, max_lines: int,
    reminder_days: list[int] | None = None, reserve: int = 0,
) -> list[str]:
    who = e(mask_email(run.email, mask))
    head = [
        f"{run.status_icon} <b>{e(run.alias)}</b>　<code>{who}</code>"
        + (f"　<i>({run.index}/{run.total})</i>" if run.total > 1 else ""),
    ]

    mode_txt = "委派權限 refresh_token" if run.mode == "delegated" else "應用程式權限 client_credentials"
    key_line = f"🔑 {e(mode_txt)}"
    if run.rotated:
        key_line += "　🔄 token 已輪換"
    head.append(key_line)

    if run.display_name:
        head.append(f"🙋 {e(run.display_name)}")

    # token 掛掉：直接給修復指引，不再列明細
    if not run.token_ok:
        head.append(f"\n❗ <b>取得 token 失敗</b>\n<code>{e(run.token_error)}</code>")
        if run.token_fatal:
            if run.mode == "delegated":
                head.append(
                    "👉 這組 refresh token 已經失效，需要人工重新授權：\n"
                    "　 在本機執行 <code>python tools/get_token.py</code>，"
                    "把新的 refresh_token 更新到 <code>E5_ACCOUNTS</code> Secret。"
                )
            else:
                head.append(
                    "👉 應用程式權限失效，通常是 client secret 過期或權限被撤銷：\n"
                    "　 到 Azure 入口網站 → 應用程式註冊 → 憑證及密碼，"
                    "產生新的密碼後更新 <code>E5_ACCOUNTS</code> Secret。"
                )
        head.append("ℹ️ 其他帳號不受影響，已照常繼續執行。")
        return ["\n".join(head)]

    head.append(
        f"📊 {run.ok_count} ✅　{run.warn_count} ⚠️　{run.fail_count} ❌"
        f"　（共 {run.total_count}）　⏱️ {e(fmt_duration(run.duration))}"
    )

    sub = run.subscription
    if sub and sub.days_left is not None:
        head.append(f"{_sub_icon(sub.days_left, reminder_days)} 訂閱 <code>{e(sub.sku)}</code> "
                    f"剩 <b>{sub.days_left}</b> 天（{e(sub.next_date)}）")
    elif sub and sub.error:
        head.append(f"ℹ️ 訂閱資訊查詢失敗：{e(sub.error)}")

    header_text = "\n".join(head)
    if detail_mode != "full" or not run.results:
        return [header_text]

    # 失敗的排前面，確保就算被截斷也一定看得到
    ordered = sorted(run.results, key=lambda r: (r.counts_as_success, r.ok))
    lines = [fmt_result_line(r) for r in ordered]
    hidden = 0
    if len(lines) > max_lines:
        hidden = len(lines) - max_lines
        lines = lines[:max_lines]

    chunks = _pack(header_text, lines, reserve)
    if hidden:
        chunks[-1] += f"\n<i>…另有 {hidden} 項未列出（可調 config.yml 的 max_detail_lines）</i>"
    return chunks


def _pack(header: str, lines: list[str], reserve: int = 0) -> list[str]:
    """把明細塞進 <pre> 區塊，超過長度就自動分成多則。

    reserve = 呼叫端之後還要黏上去的字數（執行標頭、日誌連結、token 回寫說明）。
    """
    out: list[str] = []
    current: list[str] = []
    overhead = len("<pre></pre>") + 60          # 標籤 + 摺疊提示的餘裕
    budget = LIMIT - len(header) - reserve - overhead

    for line in lines:
        safe = e(line[:400])          # 保險：單行再長也不會撐爆一則訊息
        if current and sum(len(x) + 1 for x in current) + len(safe) > budget:
            out.append(_wrap(header if not out else "", current))
            current = []
            budget = LIMIT - reserve - overhead
        current.append(safe)
    if current:
        out.append(_wrap(header if not out else "", current))
    return out or [header]


def _wrap(header: str, lines: list[str]) -> str:
    body = "<pre>" + "\n".join(lines) + "</pre>"
    return (header + "\n" + body) if header else body


def _enforce_limit(messages: list[str], hard: int = 4096) -> list[str]:
    """最後一道保險：真的還是有訊息超長時，沿著行界安全切開，不切壞 <pre>。"""
    out: list[str] = []
    for msg in messages:
        if len(msg) <= hard:
            out.append(msg)
            continue
        in_pre = False
        chunk: list[str] = []
        size = 0
        for line in msg.split("\n"):
            extra = len(line) + 1 + (len("</pre>") if in_pre else 0)
            if chunk and size + extra > hard - 16:
                out.append("\n".join(chunk) + ("</pre>" if in_pre else ""))
                chunk = ["<pre>"] if in_pre else []
                size = len(chunk[0]) if chunk else 0
            chunk.append(line)
            size += len(line) + 1
            in_pre = (in_pre or "<pre>" in line) and "</pre>" not in line
        if chunk:
            out.append("\n".join(chunk) + ("</pre>" if in_pre else ""))
    return out


def _sub_icon(days: int, thresholds: list[int] | None = None) -> str:
    """依 config.yml 的 reminder_days 決定緊急程度圖示（越接近到期越急）。"""
    steps = sorted({int(x) for x in (thresholds or [30, 14, 7, 3, 1])})
    icons = ["🚨", "⏰", "🔔", "📅", "🗓️"]
    for i, limit in enumerate(steps):
        if days <= limit:
            return icons[min(i, len(icons) - 1)]
    return "⏳"


def _job_link() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not repo or not run_id:
        return ""
    return f'🔗 <a href="{server}/{repo}/actions/runs/{run_id}">查看完整執行日誌</a>'


# ══════════════════ 小工具 ══════════════════

def e(text) -> str:
    """HTML 跳脫。"""
    return html.escape(str(text), quote=False)


def _strip_tags(text: str) -> str:
    import re

    return html.unescape(re.sub(r"<[^>]+>", "", text))
