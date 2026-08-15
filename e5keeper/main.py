"""指令列進入點。

  python -m e5keeper run                 # 排程保活（隨機抽 API）
  python -m e5keeper run --jitter        # 同上，但先隨機等一段時間再開始
  python -m e5keeper test --account all  # 測試模式：跑全部 API、不延遲
  python -m e5keeper test --account 主帳號 --category mail
  python -m e5keeper test --account all --dry-run
  python -m e5keeper poll                # 收 Telegram 指令
  python -m e5keeper report              # 送出每週統計
  python -m e5keeper validate            # 只檢查設定，不呼叫任何 API
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time

from . import history, notify, report as report_mod
from .config import Settings, load_settings
from .ghsecrets import save_accounts
from .runner import RunReport, run_all
from .utils import fmt_duration, log, mask_email, scrub_public, section


# ══════════════════ 共用流程 ══════════════════

def _finish(settings: Settings, report: RunReport) -> int:
    """回寫 token → 通知 → 寫歷史 → 決定結束碼。

    先回寫再通知，這樣通知裡就能直接告訴你 token 有沒有存好。
    """
    rotated = [a for a in report.accounts if a.rotated]
    if rotated:
        section("回寫 refresh token")
        names = "、".join(a.alias for a in rotated)
        if settings.can_write_secrets:
            ok, msg = save_accounts(settings, settings.accounts)
            log(msg, level="ok" if ok else "warn")
            report.secret_note = (
                f"💾 {notify.e(names)} 的 refresh token 已輪換並成功回寫 Secret"
                if ok else
                f"⚠️ {notify.e(names)} 的 refresh token 已輪換，但<b>回寫失敗</b>："
                f"<code>{notify.e(msg)}</code>\n"
                f"　 舊 token 短期內通常還能用，但請盡快處理，否則之後會登入失敗。"
            )
        else:
            log("未設定 GH_PAT，輪換後的 token 不會被保存", level="warn")
            report.secret_note = (
                f"ℹ️ {notify.e(names)} 的 refresh token 已輪換，但未設定 <code>GH_PAT</code>，"
                f"所以沒有回寫。Microsoft 通常仍接受舊 token，"
                f"但建議設定 PAT 讓它自動保存。"
            )

    section("送出通知")
    notify.send_report(settings, report)

    if settings.features.get("history"):
        section("寫入歷史紀錄")
        history.record(settings, report)

    _job_summary(settings, report)

    ok, warn, fail, total = report.totals
    alive = sum(1 for a in report.accounts if a.token_ok)
    section("結束")
    log(f"帳號 {alive}/{len(report.accounts)} 通過驗證　API {ok} ✅ / {warn} ⚠️ / {fail} ❌"
        f"　總耗時 {fmt_duration(report.duration)}")

    if report.accounts and alive == 0:
        log("所有帳號都無法取得 token，請重新授權", level="err")
        return 1
    return 0


def _job_summary(settings: Settings, report: RunReport) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    ok, warn, fail, total = report.totals
    mask = settings.notify.get("mask_email", True)
    lines = [
        f"## {'🧪 測試模式' if report.mode == 'test' else '🛡️ 保活執行'}",
        "",
        f"觸發：`{report.trigger}`　耗時：{fmt_duration(report.duration)}　"
        f"API：{ok} ✅ / {warn} ⚠️ / {fail} ❌（共 {total}）",
        "",
        "| 狀態 | 帳號 | 信箱 | ✅ | ⚠️ | ❌ | 訂閱剩餘 |",
        "|:--:|---|---|--:|--:|--:|--:|",
    ]
    for a in report.accounts:
        days = a.subscription.days_left if a.subscription else None
        lines.append(
            f"| {a.status_icon} | {a.alias} | `{mask_email(a.email, mask)}` | "
            f"{a.ok_count} | {a.warn_count} | {a.fail_count} | "
            f"{f'{days} 天' if days is not None else '—'} |"
        )
    # Job Summary 顯示在 Actions 的執行頁面上，公開 repo 等於公開，所以要清洗。
    # 完整未清洗的細節只會出現在你的 Telegram 私訊裡。
    for a in report.accounts:
        extra = (a.email,) if a.email else ()
        if not a.token_ok:
            lines += ["", f"> 🚫 **{a.alias}** 取得 token 失敗："
                          f"`{scrub_public(a.token_error, extra)}`"]
        bad = [r for r in a.results if not r.counts_as_success]
        if bad:
            lines += ["", f"<details><summary>{a.alias} 的失敗明細（{len(bad)}）</summary>", ""]
            lines += [f"- `{r.spec_id}` → {r.status} {scrub_public(r.error, extra)}"
                      for r in bad]
            lines += ["", "</details>"]
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass


# ══════════════════ 各子指令 ══════════════════

def cmd_run(args) -> int:
    settings = load_settings()

    if args.jitter:
        max_min = int(settings.raw["schedule"].get("random_delay_max_minutes", 0) or 0)
        if max_min > 0:
            delay = random.randint(0, max_min * 60)
            log(f"隨機延遲 {delay // 60} 分 {delay % 60} 秒後開始（讓每天執行時刻都不一樣）")
            time.sleep(delay)

    section(f"開始保活 · 共 {len(settings.accounts)} 個帳號")
    rep = run_all(settings, test_mode=False, trigger=args.trigger)
    return _finish(settings, rep)


def cmd_test(args) -> int:
    settings = load_settings()
    key = (args.account or "all").strip()

    if key.lower() in ("all", "全部", "*"):
        targets = [a for a in settings.accounts if a.enabled]
        who = f"全部 {len(targets)} 個帳號"
    else:
        acc = settings.find_account(key)
        if acc is None:
            available = "、".join(a.alias for a in settings.accounts)
            log(f"找不到帳號「{key}」。可用的有：{available}", level="err")
            if settings.telegram_token:
                notify.send_text(
                    settings,
                    f"❌ 找不到帳號 <code>{notify.e(key)}</code>\n"
                    f"可用的帳號：{notify.e(available)}",
                )
            return 1
        targets = [acc]
        who = acc.alias

    trigger = args.trigger or "manual"
    section(f"測試模式 · {who}" + (f" · 只測 {args.category}" if args.category else ""))
    rep = run_all(
        settings,
        test_mode=True,
        accounts=targets,
        trigger=trigger,
        only_category=args.category or "",
        dry_run=args.dry_run,
    )
    return _finish(settings, rep)


def cmd_authorize(args) -> int:
    from . import device_auth

    settings = load_settings(allow_empty_accounts=True)
    tenant = (args.tenant or "common").strip() or "common"
    client_id = (args.client_id or "").strip()
    if not client_id:
        log("必須提供 --client-id（Azure 應用程式的用戶端 ID）", level="err")
        return 2

    return device_auth.authorize(
        settings,
        alias=(args.alias or "").strip(),
        tenant=tenant,
        client_id=client_id,
        client_secret=(args.client_secret or "").strip(),
        replace=args.replace,
        expected_upn=(args.expect or "").strip(),
    )


def cmd_poll(_args) -> int:
    from .telegram_poll import poll

    settings = load_settings()
    return poll(settings)


def cmd_report(args) -> int:
    settings = load_settings()
    report_mod.weekly(settings, days=args.days, force=args.force)
    return 0


def _check_github_token(settings: Settings) -> list[str]:
    """實際打 GitHub API 確認 PAT 的兩項權限（Contents、Secrets）都夠。"""
    import requests

    from . import gitapi

    problems: list[str] = []
    repo = settings.repo or os.environ.get("GITHUB_REPOSITORY", "").strip()
    token, source = gitapi.pick_token()

    if not token:
        problems.append("缺少 GH_PAT：refresh token 不會自動回寫，commit 也不會有 Verified 標記")
        return problems
    if not repo:
        log(f"找到 token（{source}），但本機沒有 GITHUB_REPOSITORY，跳過線上權限檢查", level="warn")
        return problems

    login = gitapi.whoami(token)
    log(f"GitHub token 來源：{source}" + (f"　身分：{login}" if login else ""), level="ok")

    # Contents 寫入權 → 決定 commit 能不能帶簽章
    try:
        oid = gitapi.get_head_oid(token, repo, os.environ.get("GITHUB_REF_NAME", "main"))
        log(f"Contents 讀取正常（HEAD {oid[:7]}），可建立 Verified commit", level="ok")
    except gitapi.CommitError as exc:
        problems.append(f"Contents 權限可能不足：{exc}（commit 會退回無 Verified 的一般推送）")

    # Secrets 寫入權 → 決定 refresh token 能不能自動續命
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
            headers={"Authorization": f"Bearer {token}",
                     "Accept": "application/vnd.github+json"},
            timeout=20,
        )
        if resp.ok:
            log("Secrets 權限正常，refresh token 可自動回寫", level="ok")
        else:
            problems.append(
                f"Secrets 權限不足（HTTP {resp.status_code}）："
                "細粒度 PAT 請把 Secrets 設成 Read and write"
            )
    except requests.RequestException as exc:
        problems.append(f"檢查 Secrets 權限時連線失敗：{exc}")

    return problems


def cmd_validate(_args) -> int:
    settings = load_settings()
    section("設定檢查")
    problems: list[str] = []

    log(f"config.yml 載入成功，時區 {settings.tz}", level="ok")
    log(f"帳號數：{len(settings.accounts)}", level="ok")
    for a in settings.accounts:
        state = "啟用" if a.enabled else "停用"
        log(f"  · {a.alias}｜{mask_email(a.email)}｜{a.mode_label}｜{state}")
        if a.mode == "delegated" and len(a.refresh_token) < 50:
            problems.append(f"{a.alias} 的 refresh_token 看起來太短，可能貼錯了")

    if settings.telegram_token and settings.telegram_chat_id:
        log("Telegram 設定齊全", level="ok")
    else:
        problems.append("缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，不會有任何通知")

    problems += _check_github_token(settings)

    from . import apis
    for a in settings.accounts:
        pool = apis.available(
            a, settings.categories,
            allow_write=bool(settings.features["write_operations"]),
            allow_self_mail=bool(settings.features["send_self_mail"]),
        )
        log(f"  · {a.alias} 可用 API：{len(pool)} 個")
        if len(pool) < settings.run["min_apis"]:
            problems.append(f"{a.alias} 可用 API 只有 {len(pool)} 個，少於 min_apis")

    print()
    if problems:
        for p in problems:
            log(p, level="warn")
        log(f"檢查完成，有 {len(problems)} 項提醒", level="warn")
    else:
        log("全部檢查通過 🎉", level="ok")
    return 0


# ══════════════════ 入口 ══════════════════

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="e5keeper", description="Microsoft E5 開發者訂閱保活精靈")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="排程保活")
    r.add_argument("--jitter", action="store_true", help="開始前先隨機等待")
    r.add_argument("--trigger", default="cron", help="標記本次觸發來源")
    r.set_defaults(func=cmd_run)

    t = sub.add_parser("test", help="測試模式：跑指定帳號的全部 API")
    t.add_argument("--account", "-a", default="all", help="帳號別名 / email / 序號，或 all")
    t.add_argument("--category", "-c", default="", help="只測某一類：mail / files / calendar / directory")
    t.add_argument("--dry-run", action="store_true", help="只驗證 token 與權限，不實際呼叫 API")
    t.add_argument("--trigger", default="manual", help="標記本次觸發來源")
    t.set_defaults(func=cmd_test)

    a = sub.add_parser("authorize", help="裝置碼授權，直接把結果寫進 E5_ACCOUNTS Secret")
    a.add_argument("--alias", default="", help="這個帳號的顯示別名")
    a.add_argument("--tenant", default="common", help="租用戶，個人自建的 E5 用 common")
    a.add_argument("--client-id", default="", help="Azure 應用程式的用戶端 ID")
    a.add_argument("--client-secret", default="", help="公用用戶端不需要，留空即可")
    a.add_argument("--expect", default="",
                   help="預期登入的帳號 UPN，不符就中止（防止裝置碼被別人兌換）")
    a.add_argument("--replace", action="store_true", default=True,
                   help="同一個 email 已存在時覆寫（預設開啟）")
    a.add_argument("--no-replace", dest="replace", action="store_false",
                   help="同一個 email 已存在時中止，不覆寫")
    a.set_defaults(func=cmd_authorize)

    pl = sub.add_parser("poll", help="收取並執行 Telegram 指令")
    pl.set_defaults(func=cmd_poll)

    rp = sub.add_parser("report", help="送出統計報告")
    rp.add_argument("--days", type=int, default=7)
    rp.add_argument("--force", action="store_true",
                    help="即使 config.yml 關掉 weekly_report 也照送")
    rp.set_defaults(func=cmd_report)

    v = sub.add_parser("validate", help="檢查設定是否完整（不呼叫任何 API）")
    v.set_defaults(func=cmd_validate)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ValueError as exc:
        log(str(exc), level="err")
        return 2
    except KeyboardInterrupt:
        log("使用者中斷", level="warn")
        return 130


if __name__ == "__main__":
    sys.exit(main())
