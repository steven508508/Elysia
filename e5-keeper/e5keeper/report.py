"""每週成功率統計報告。"""

from __future__ import annotations

from collections import Counter, defaultdict

from . import history
from .notify import DIVIDER, e, send_text
from .utils import fmt_time, log, now_local


def weekly(settings, days: int = 7, *, force: bool = False) -> bool:
    if not force and not settings.features.get("weekly_report", True):
        log("config.yml 已關閉 weekly_report，略過統計報告")
        return False

    entries = history.load_entries(days)
    if not entries:
        send_text(settings, f"📈 <b>E5 保活精靈 · 週報</b>\n\n過去 {days} 天沒有任何執行紀錄。")
        return False

    runs = len(entries)
    per_account: dict[str, dict] = defaultdict(
        lambda: {"ok": 0, "warn": 0, "fail": 0, "runs": 0, "token_fail": 0,
                 "rotated": 0, "days_left": None, "sku": "", "email": ""}
    )
    fail_counter: Counter = Counter()
    total_ok = total_warn = total_fail = 0

    for item in entries:
        for acc in item.get("accounts") or []:
            slot = per_account[acc.get("alias", "?")]
            slot["runs"] += 1
            slot["ok"] += acc.get("ok", 0)
            slot["warn"] += acc.get("warn", 0)
            slot["fail"] += acc.get("fail", 0)
            slot["email"] = acc.get("email", "")     # history_detail=minimal 時不存在
            if not acc.get("token_ok", True):
                slot["token_fail"] += 1
            if acc.get("rotated"):
                slot["rotated"] += 1
            if acc.get("days_left") is not None:
                slot["days_left"] = acc["days_left"]
                slot["sku"] = acc.get("sku", "")
            total_ok += acc.get("ok", 0)
            total_warn += acc.get("warn", 0)
            total_fail += acc.get("fail", 0)
            for f in acc.get("fails") or []:
                fail_counter[f"{f.get('id', '?')} ({f.get('status', '?')})"] += 1

    total_calls = total_ok + total_warn + total_fail
    rate = (total_ok + total_warn) / total_calls * 100 if total_calls else 0.0
    icon = "🟢" if rate >= 98 else ("🟡" if rate >= 90 else "🔴")

    lines = [
        "📈 <b>E5 保活精靈 · 每週統計</b>",
        f"🕐 {e(fmt_time(now_local(settings.tz)))}　<i>過去 {days} 天</i>",
        DIVIDER,
        f"{icon} 整體健康度：<b>{rate:.1f}%</b>",
        f"🔁 執行 {runs} 次　📊 API 呼叫 {total_calls} 次",
        f"　　{total_ok} ✅　{total_warn} ⚠️　{total_fail} ❌",
        "",
        "<b>各帳號</b>",
    ]

    rows = []
    for alias, s in sorted(per_account.items()):
        calls = s["ok"] + s["warn"] + s["fail"]
        acc_rate = (s["ok"] + s["warn"]) / calls * 100 if calls else 0.0
        mark = "🟢" if acc_rate >= 98 and not s["token_fail"] else \
               ("🔴" if s["token_fail"] or acc_rate < 90 else "🟡")
        rows.append(f"{mark} {alias}  {acc_rate:5.1f}%  {s['ok']}✅ {s['warn']}⚠️ {s['fail']}❌")
        extra = []
        if s["token_fail"]:
            extra.append(f"token 失敗 {s['token_fail']} 次")
        if s["rotated"]:
            extra.append(f"token 輪換 {s['rotated']} 次")
        if s["days_left"] is not None:
            extra.append(f"訂閱剩 {s['days_left']} 天")
        if extra:
            rows.append(f"    └ {'、'.join(extra)}")

    lines.append("<pre>" + e("\n".join(rows)) + "</pre>")

    if fail_counter:
        top = fail_counter.most_common(8)
        lines += ["", "<b>最常失敗的 API</b>"]
        lines.append("<pre>" + e("\n".join(f"{n:>3} 次  {name}" for name, n in top)) + "</pre>")
    else:
        lines += ["", "🎉 這週完全沒有失敗的 API。"]

    warn_accounts = [a for a, s in per_account.items()
                     if s["days_left"] is not None and s["days_left"] <= 30]
    if warn_accounts:
        lines.append("")
        for alias in warn_accounts:
            s = per_account[alias]
            lines.append(f"⏰ <b>{e(alias)}</b> 訂閱只剩 <b>{s['days_left']}</b> 天，記得續訂。")
    elif not any(s["days_left"] is not None for s in per_account.values()):
        # history_detail=minimal：到期資訊刻意不寫進公開紀錄，改看每次執行的通知
        lines += ["", "<i>ℹ️ 訂閱到期資訊不寫入公開紀錄，請看每次保活執行的通知。</i>"]

    send_text(settings, "\n".join(lines))
    log("週報已送出", level="ok")
    return True
