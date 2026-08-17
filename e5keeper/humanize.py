"""擬人化：讓行為模式不要一眼就看出是排程機器人。

要解決的是「節奏」問題，不是「內容」問題。原本的模式：

  · 每天不多不少剛好 3 次，永遠在同樣 3 個時段
  · 每次呼叫之間都均勻停 1~8 秒
  · 每次都跑訂閱查詢，位置固定在最後
  · 建立的檔案 0.9 秒後就刪掉
  · 週末跟平日一模一樣

真人不是這樣。真人有些天用得多、有些天幾乎不碰；操作是叢發式的
（連續點幾下，然後離開去做別的事）；週末通常少用。

這個模組做四件事：

  ① 每天的行程     從 8 個候選時段裡隨機挑幾個來跑，週末挑更少
  ② 呼叫節奏       多數是連續操作（1 秒內），偶爾停下來「閱讀」（數十秒）
  ③ 延後清除       建立的東西留到本輪結束才刪，存活數分鐘而不是 1 秒
  ④ 非必要動作     訂閱查詢改成機率性，偶爾重複打開同一個東西

⚠️ 誠實說明：微軟的續訂判準沒有公開，沒有人能保證這些有用。
   合理推測納入評估的是 API 呼叫的量、種類與時間分布 —— 而「時間分布」
   正是這個模組在改善的東西，所以它比改檔案內容更值得做。
   但仍然是推測，不是保證。
"""

from __future__ import annotations

import random
from datetime import datetime, timezone

from .utils import get_tz, log

# 候選時段（UTC）。換算成台北時間大致覆蓋一般人的清醒時間：
#   08:37 10:13 12:41 14:29 17:07 19:53 22:19 00:31
# 刻意都避開整點與半點 —— 那是 GitHub 排程最壅塞的時候，也最像機器。
DEFAULT_SLOTS = ["00:37", "02:13", "04:41", "06:29", "09:07", "11:53", "14:19", "16:31"]

# 排程可能延遲，所以用「離現在最近的時段」來判斷自己是哪一班，容忍度寬一點
SLOT_TOLERANCE_MINUTES = 100


def _minutes(hhmm: str) -> int:
    h, _, m = hhmm.partition(":")
    return int(h) * 60 + int(m)


def _day_rng(day: str, salt: str) -> random.Random:
    """同一天的所有 job 必須算出同一份行程，所以用日期當種子。

    加 salt（repo 名稱）是為了讓不同使用者的行程不同 ——
    否則全世界跑這套工具的人會在同一時間一起打微軟。
    """
    return random.Random(f"{day}|{salt}")


def todays_slots(
    slots: list[str],
    runs_range: tuple[int, int],
    weekend_range: tuple[int, int],
    tz_name: str,
    salt: str = "",
    now: datetime | None = None,
) -> list[str]:
    """決定今天要在哪幾個時段執行。"""
    now = now or datetime.now(timezone.utc)
    # 「哪一天」一律以**當地日期**判定，種子和週末判斷都用它。
    # 不能一個用 UTC 日期、一個用當地時間 —— 最後那個時段（UTC 16:31）
    # 換算到台北是隔天 00:31，兩種基準會讓同一班算出不同的行程。
    local = now.astimezone(get_tz(tz_name))
    is_weekend = local.weekday() >= 5

    lo, hi = weekend_range if is_weekend else runs_range
    lo = max(1, min(int(lo), len(slots)))
    hi = max(lo, min(int(hi), len(slots)))

    rng = _day_rng(local.strftime("%Y-%m-%d"), salt)
    k = rng.randint(lo, hi)
    return sorted(rng.sample(list(slots), k), key=_minutes)


def should_run_now(settings, now: datetime | None = None) -> tuple[bool, str]:
    """這一班該不該跑？回傳 (要不要跑, 說明)。"""
    cfg = settings.raw.get("humanize") or {}
    if not cfg.get("enabled", True):
        return True, "擬人化排程已關閉，照常執行"

    slots = list(cfg.get("slots") or DEFAULT_SLOTS)
    runs = tuple(cfg.get("runs_per_day") or (2, 5))
    weekend = tuple(cfg.get("weekend_runs_per_day") or (1, 3))

    import os

    salt = os.environ.get("GITHUB_REPOSITORY", "") or "local"
    now = now or datetime.now(timezone.utc)
    chosen = todays_slots(slots, runs, weekend, settings.tz, salt, now)

    # 我是哪一班？取離現在最近的時段
    current = now.hour * 60 + now.minute
    nearest, gap = None, 10**9
    for s in slots:
        d = abs(_minutes(s) - current)
        d = min(d, 1440 - d)          # 跨午夜
        if d < gap:
            nearest, gap = s, d

    if nearest is None or gap > SLOT_TOLERANCE_MINUTES:
        return True, f"對不上任何時段（最近的差 {gap} 分），保險起見照跑"

    plan = "、".join(chosen)
    if nearest in chosen:
        return True, f"今天排 {len(chosen)} 次（{plan}），現在這班 {nearest} 要跑"
    return False, f"今天排 {len(chosen)} 次（{plan}），這班 {nearest} 不在名單內"


# ══════════════════ 呼叫節奏 ══════════════════

def next_delay(cfg: dict, fallback: tuple | list = (1, 8)) -> float:
    """兩次 API 呼叫之間該停多久。

    真人的操作是叢發式的：開一個畫面會連續觸發好幾個請求（間隔不到一秒），
    然後停下來看內容（數十秒），偶爾整個離開一下（數分鐘）。
    均勻分布的 1~8 秒反而是最不像人的形狀。

    fallback = config.yml 的 run.api_delay_seconds。擬人化關掉時用它；
    它若設成 [0, 0]（測試會這樣做）就完全不等待。
    """
    try:
        f_lo, f_hi = float(fallback[0]), float(fallback[1])
    except (TypeError, ValueError, IndexError):
        f_lo, f_hi = 1.0, 8.0

    # 明確設成不等待時，擬人化也要讓路 —— 否則離線測試會跑上好幾分鐘
    if f_hi <= 0:
        return 0.0
    if not cfg.get("enabled", True):
        return random.uniform(f_lo, f_hi)

    if random.random() < float(cfg.get("long_pause_probability", 0.08)):
        lo, hi = cfg.get("long_pause_delay") or (60, 240)
    elif random.random() < float(cfg.get("burst_probability", 0.6)):
        lo, hi = cfg.get("burst_delay") or (0.3, 2)
    else:
        lo, hi = cfg.get("pause_delay") or (5, 60)
    return random.uniform(float(lo), float(hi))


def roll(cfg: dict, key: str, default: float) -> bool:
    """依設定的機率擲一次骰子。"""
    if not cfg.get("enabled", True):
        return True
    return random.random() < float(cfg.get(key, default))


def describe(settings) -> str:
    """把今天的行程寫進日誌，方便你對照。"""
    cfg = settings.raw.get("humanize") or {}
    if not cfg.get("enabled", True):
        return ""
    import os

    salt = os.environ.get("GITHUB_REPOSITORY", "") or "local"
    chosen = todays_slots(
        list(cfg.get("slots") or DEFAULT_SLOTS),
        tuple(cfg.get("runs_per_day") or (2, 5)),
        tuple(cfg.get("weekend_runs_per_day") or (1, 3)),
        settings.tz, salt,
    )
    tz = get_tz(settings.tz)
    local = []
    for s in chosen:
        h, m = divmod(_minutes(s), 60)
        dt = datetime.now(timezone.utc).replace(hour=h, minute=m, second=0, microsecond=0)
        local.append(dt.astimezone(tz).strftime("%H:%M"))
    return f"今天的行程（{settings.tz}）：{'、'.join(local)}　共 {len(chosen)} 次"
