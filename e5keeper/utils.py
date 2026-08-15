"""共用小工具：遮蔽、時間、隨機、日誌。"""

from __future__ import annotations

import os
import random
import re
import sys
import time
from datetime import datetime, timezone, timedelta

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None


# ── 敏感資訊遮蔽 ──────────────────────────────────────────────

def mask_email(addr: str, enabled: bool = True) -> str:
    """user@example.com → us***@example.com"""
    if not addr:
        return "(未知帳號)"
    if not enabled:
        return addr
    if "@" not in addr:
        return addr[:2] + "***"
    local, _, domain = addr.partition("@")
    if len(local) <= 2:
        head = local[:1]
    else:
        head = local[:2]
    return f"{head}***@{domain}"


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_GUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)


def scrub_public(text: str, extra: tuple[str, ...] = ()) -> str:
    """把要寫進「公開場所」的文字清乾淨。

    公開場所 = commit 進 repo 的 history／STATUS.md、Actions 的 Job Summary、
    以及公開 repo 那份任何人都讀得到的執行日誌。

    Microsoft Graph 的錯誤訊息會原封不動回吐你的完整 email
    （例如 Request_ResourceNotFound: Resource 'admin@xxx.onmicrosoft.com' does not exist），
    AADSTS 的錯誤描述則常常夾帶租用戶 ID、應用程式 ID 與 Trace ID。
    這些單獨看都不是密碼，但湊在一起足以定位到你的租用戶，所以一律洗掉。

    email 是整個換成 <email>，而不是遮成 ab***@xx.com —— 遮蔽版仍保留完整網域，
    光靠網域就能查出你的 Azure 租用戶 ID，再配上局部的帳號名稱就足以做列舉。
    公開場所留半個地址沒有除錯價值（別名已經能認出是哪個帳號了）。

    送到 Telegram 的內容不走這裡 —— 那是你的私人對話，保留完整細節才好除錯。
    """
    if not text:
        return ""
    out = str(text)
    for term in extra:
        if term and len(term) > 3:
            out = out.replace(term, "<email>")
    out = _EMAIL_RE.sub("<email>", out)
    out = _GUID_RE.sub("<id>", out)
    return out


def in_public_log() -> bool:
    """目前的 stdout 是不是會被公開？（公開 repo 的 Actions 日誌是任何人都看得到的）"""
    return bool(os.environ.get("GITHUB_ACTIONS"))


def mask_secret(value: str, keep: int = 4) -> str:
    """把 token / secret 縮成 abcd…(共 1234 字)，只用於日誌。"""
    if not value:
        return "(空)"
    return f"{value[:keep]}…(共 {len(value)} 字)"


def gh_add_mask(*values: str) -> None:
    """要求 GitHub Actions 在日誌裡把這些字串打成 ***。"""
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    for v in values:
        if v and len(str(v)) >= 8:
            print(f"::add-mask::{v}", flush=True)


# ── 時間 ─────────────────────────────────────────────────────

def get_tz(name: str):
    if ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone(timedelta(hours=8))  # 退回 UTC+8


def now_local(tz_name: str) -> datetime:
    return datetime.now(get_tz(tz_name))


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def fmt_duration(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def parse_iso(value: str):
    """容忍 Graph 回傳的各種 ISO8601 尾巴。"""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:
        head, _, tail = text.partition(".")
        frac = tail
        offset = ""
        for marker in ("+", "-"):
            idx = tail.find(marker)
            if idx > 0:
                frac, offset = tail[:idx], tail[idx:]
                break
        text = f"{head}.{frac[:6]}{offset}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# ── 隨機 ─────────────────────────────────────────────────────

def jitter_sleep(bounds, label: str = "") -> float:
    """依 [下界, 上界] 隨機睡一下，回傳實際睡了幾秒。"""
    try:
        low, high = float(bounds[0]), float(bounds[1])
    except (TypeError, ValueError, IndexError):
        return 0.0
    if high <= 0:
        return 0.0
    low = max(0.0, min(low, high))
    delay = random.uniform(low, high)
    if delay > 0:
        if label:
            log(f"隨機等待 {delay:.1f}s（{label}）", level="dim")
        time.sleep(delay)
    return delay


# ── 日誌 ─────────────────────────────────────────────────────

_LEVEL_ICON = {
    "info": "·",
    "ok": "✓",
    "warn": "!",
    "err": "✗",
    "dim": " ",
}


def log(message: str, level: str = "info") -> None:
    icon = _LEVEL_ICON.get(level, "·")
    stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{stamp}] {icon} {message}", flush=True)


def section(title: str) -> None:
    print("", flush=True)
    print("━" * 62, flush=True)
    print(f"  {title}", flush=True)
    print("━" * 62, flush=True)


def die(message: str, code: int = 1):
    log(message, level="err")
    sys.exit(code)


def truncate(text: str, limit: int) -> str:
    text = str(text).replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"
