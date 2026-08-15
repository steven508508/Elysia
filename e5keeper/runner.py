"""執行引擎：跑一輪保活，或跑一次測試。"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import apis
from .auth import TokenError, acquire_token
from .config import Account, Settings
from .graph import ApiResult, GraphClient
from .utils import (
    fmt_duration, in_public_log, jitter_sleep, log, mask_email,
    scrub_public, section, truncate,
)


@dataclass
class Subscription:
    sku: str = ""
    status: str = ""
    days_left: int | None = None
    next_date: str = ""
    error: str = ""


@dataclass
class AccountRun:
    alias: str
    email: str
    mode: str
    index: int = 0
    total: int = 1
    display_name: str = ""
    token_ok: bool = False
    token_error: str = ""
    token_fatal: bool = False
    rotated: bool = False
    results: list[ApiResult] = field(default_factory=list)
    subscription: Subscription | None = None
    duration: float = 0.0
    planned: int = 0

    @property
    def ok_count(self) -> int:
        return sum(1 for r in self.results if r.ok)

    @property
    def warn_count(self) -> int:
        return sum(1 for r in self.results if r.tolerated)

    @property
    def fail_count(self) -> int:
        return sum(1 for r in self.results if not r.counts_as_success)

    @property
    def total_count(self) -> int:
        return len(self.results)

    @property
    def healthy(self) -> bool:
        return self.token_ok and self.fail_count == 0

    @property
    def status_icon(self) -> str:
        if not self.token_ok:
            return "🚫"
        if self.fail_count:
            return "❌"
        if self.warn_count:
            return "⚠️"
        return "✅"


@dataclass
class RunReport:
    mode: str = "schedule"            # schedule | test
    trigger: str = "cron"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    duration: float = 0.0
    accounts: list[AccountRun] = field(default_factory=list)
    note: str = ""
    secret_note: str = ""        # refresh token 回寫結果（有輪換時才會有值）
    health_note: str = ""        # 指令通道健康檢查（有異常時才會有值）

    @property
    def all_healthy(self) -> bool:
        return bool(self.accounts) and all(a.healthy for a in self.accounts)

    @property
    def totals(self) -> tuple[int, int, int, int]:
        ok = sum(a.ok_count for a in self.accounts)
        warn = sum(a.warn_count for a in self.accounts)
        fail = sum(a.fail_count for a in self.accounts)
        return ok, warn, fail, ok + warn + fail


# ══════════════════ 訂閱到期偵測 ══════════════════

def check_subscription(client: GraphClient) -> Subscription:
    sub = Subscription()
    status, payload, error, _, _ = client.request(
        "GET", "/directory/subscriptions", beta=True, retry=False
    )
    if not (200 <= status < 300) or not isinstance(payload, dict):
        sub.error = truncate(error or f"HTTP {status}", 80)
        return sub

    items = payload.get("value") or []
    if not items:
        sub.error = "查無訂閱資料"
        return sub

    def score(item: dict) -> int:
        name = (item.get("skuPartNumber") or "").upper()
        if "DEVELOPER" in name:
            return 3
        if "E5" in name or "ENTERPRISEPREMIUM" in name:
            return 2
        return 1

    best = sorted(items, key=score, reverse=True)[0]
    sub.sku = best.get("skuPartNumber", "") or "(未知)"
    sub.status = best.get("status", "") or ""
    raw_date = best.get("nextLifecycleDateTime") or ""
    if raw_date:
        from .utils import parse_iso

        dt = parse_iso(raw_date)
        if dt:
            if dt.tzinfo is None:          # 少數情況下 Graph 會回沒有時區的時間
                dt = dt.replace(tzinfo=timezone.utc)
            sub.next_date = dt.strftime("%Y-%m-%d")
            sub.days_left = (dt - datetime.now(timezone.utc)).days
    return sub


# ══════════════════ 單一帳號 ══════════════════

def run_account(
    account: Account,
    settings: Settings,
    *,
    test_mode: bool = False,
    only_category: str = "",
    dry_run: bool = False,
) -> AccountRun:
    run = AccountRun(
        alias=account.alias,
        email=account.email,
        mode=account.mode,
        index=account.index,
    )
    started = time.monotonic()
    cfg_run = settings.run
    feat = settings.features
    label = f"{account.alias} · {mask_email(account.email, settings.notify['mask_email'])}"
    section(f"帳號 {label}（{account.mode_label}）")

    # 1) 取得 token
    try:
        token = acquire_token(account, timeout=int(cfg_run["timeout_seconds"]))
    except TokenError as exc:
        run.token_error = str(exc)
        run.token_fatal = exc.fatal
        run.duration = time.monotonic() - started
        log(f"取得 token 失敗：{scrub_public(str(exc), (account.email,))}", level="err")
        return run
    except Exception as exc:  # noqa: BLE001 — 單一帳號的意外絕不能拖垮其他帳號
        run.token_error = f"{exc.__class__.__name__}: {exc}"
        run.duration = time.monotonic() - started
        log(f"取得 token 時發生非預期的錯誤："
            f"{scrub_public(run.token_error, (account.email,))}", level="err")
        return run

    run.token_ok = True
    if token.rotated:
        run.rotated = True
        account.refresh_token = token.new_refresh_token

    client = GraphClient(
        access_token=token.access_token,
        user_ref=account.user_ref,
        self_address=account.target_user or account.email,
        cleanup_after_write=bool(feat.get("cleanup_after_write", True)),
        timeout=int(cfg_run["timeout_seconds"]),
        max_attempts=int(cfg_run["retry"]["max_attempts"]),
        initial_backoff=float(cfg_run["retry"]["initial_backoff"]),
        multiplier=float(cfg_run["retry"]["multiplier"]),
    )

    # 2) 決定要跑哪些 API
    categories = settings.categories
    if only_category:
        categories = [c for c in categories if c == only_category] or [only_category]

    pool = apis.available(
        account,
        categories,
        allow_write=bool(feat["write_operations"]),
        allow_self_mail=bool(feat["write_operations"] and feat["send_self_mail"]),
    )
    if not pool:
        run.duration = time.monotonic() - started
        log("目前設定下沒有任何可用的 API", level="warn")
        return run

    if test_mode:
        selected = list(pool)          # 測試模式：全部都跑，不抽樣
    else:
        selected = apis.sample(pool, int(cfg_run["min_apis"]), int(cfg_run["max_apis"]))
    run.planned = len(selected)
    log(f"本輪將呼叫 {len(selected)} / {len(pool)} 個 API"
        + ("（測試模式：全跑、不延遲）" if test_mode else ""))

    # 3) 逐一呼叫
    for i, spec in enumerate(selected, 1):
        if dry_run:
            res = ApiResult(spec_id=spec.id, name=spec.name, category=spec.category)
            res.skipped = True
            res.summary = "dry-run 未實際呼叫"
            run.results.append(res)
            continue

        log(f"[{i}/{len(selected)}] {spec.id} — {spec.name}")
        res = client.call(spec)
        run.results.append(res)

        # 這一行會進 Actions 日誌。公開 repo 的日誌是任何人都讀得到的，
        # 而 summary 裡是活生生的帳號內容（你的姓名、組織名、信件數、
        # OneDrive 用量、甚至你主管的名字）。所以在 Actions 裡只印狀態碼，
        # 完整明細留給 Telegram 私訊；本機執行時則照常全印，方便除錯。
        detail = res.summary or res.error
        if in_public_log():
            detail = scrub_public(res.error) if not res.ok else "（明細見 Telegram）"
        log(f"    → {res.icon} {res.status or 'ERR'} {detail}",
            level="ok" if res.ok else ("warn" if res.tolerated else "err"))

        if not test_mode and i < len(selected):
            jitter_sleep(cfg_run["api_delay_seconds"], label="API 間隔")

    # 4) 附帶資訊
    for res in run.results:
        if res.spec_id == "dir.me" and res.ok and res.summary:
            run.display_name = res.summary

    if feat["subscription_reminder"] and not dry_run:
        run.subscription = check_subscription(client)
        if run.subscription.days_left is not None:
            log(f"訂閱 {run.subscription.sku} 剩餘 {run.subscription.days_left} 天")

    run.duration = time.monotonic() - started
    log(f"帳號完成：{run.ok_count} 成功 / {run.warn_count} 警告 / {run.fail_count} 失敗"
        f"（{fmt_duration(run.duration)}）",
        level="ok" if run.healthy else "warn")
    return run


# ══════════════════ 整輪 ══════════════════

def run_all(
    settings: Settings,
    *,
    test_mode: bool = False,
    accounts: list[Account] | None = None,
    trigger: str = "cron",
    only_category: str = "",
    dry_run: bool = False,
) -> RunReport:
    report = RunReport(
        mode="test" if test_mode else "schedule",
        trigger=trigger,
    )
    started = time.monotonic()
    targets = accounts if accounts is not None else [a for a in settings.accounts if a.enabled]
    total = len(targets)

    if not targets:
        report.note = "沒有啟用中的帳號"
        return report

    order = list(range(total))
    if not test_mode and total > 1:
        random.shuffle(order)          # 帳號順序也打亂

    for pos, idx in enumerate(order, 1):
        account = targets[idx]
        try:
            run = run_account(
                account, settings,
                test_mode=test_mode, only_category=only_category, dry_run=dry_run,
            )
        except Exception as exc:  # noqa: BLE001 — 保底：一個帳號炸掉，其他照跑
            log(f"帳號「{account.alias}」執行時發生非預期的錯誤：{exc}", level="err")
            run = AccountRun(
                alias=account.alias, email=account.email,
                mode=account.mode, index=account.index,
                token_error=f"{exc.__class__.__name__}: {exc}",
            )
        run.index, run.total = pos, total
        report.accounts.append(run)

        if not test_mode and pos < total:
            jitter_sleep(settings.run["account_delay_seconds"], label="帳號間隔")

    report.duration = time.monotonic() - started
    return report
