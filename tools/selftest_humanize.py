#!/usr/bin/env python3
"""擬人化行為測試（離線）。

驗證：
  ① 同一個當地日的所有班次算出同一份行程（不然行程就沒意義了）
  ② 不會出現「整天零活動」的日子
  ③ 週末的執行次數確實比平日少
  ④ 不同 repo 的行程不同（避免全世界同時打微軟）
  ⑤ 呼叫節奏是叢發式的，不是均勻分布
  ⑥ 延後清除：建立的東西留到本輪結束才刪，而且真的有刪掉

用法：
    python tools/selftest_humanize.py
"""

from __future__ import annotations

import collections
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import yaml

from e5keeper import humanize

CFG = yaml.safe_load((ROOT / "config.yml").read_text(encoding="utf-8"))["humanize"]
SLOTS = CFG["slots"]
TZ = "Asia/Taipei"
SALT = "user/e5-keeper"
RUNS = tuple(CFG["runs_per_day"])
WEEKEND = tuple(CFG["weekend_runs_per_day"])

bad = 0


def case(name: str, ok: bool, detail: str = "") -> None:
    global bad
    print(f"  {'✅' if ok else '❌'} {name}" + (f"　→ {detail}" if not ok and detail else ""))
    if not ok:
        bad += 1


def plan(now):
    return humanize.todays_slots(SLOTS, RUNS, WEEKEND, TZ, SALT, now)


def main() -> int:
    tz = humanize.get_tz(TZ)

    print("① 同一個當地日的所有班次，行程必須一致")
    mismatch = 0
    for d in range(90):
        base = datetime(2026, 3, 1, tzinfo=timezone.utc) + timedelta(days=d)
        byday: dict[str, list] = {}
        for off in (-1, 0, 1):
            day = base + timedelta(days=off)
            for s in SLOTS:
                h, m = map(int, s.split(":"))
                t = day.replace(hour=h, minute=m)
                byday.setdefault(t.astimezone(tz).strftime("%Y-%m-%d"), []).append(t)
        for times in byday.values():
            if len(times) < len(SLOTS):
                continue
            if len({tuple(plan(t)) for t in times}) != 1:
                mismatch += 1
    case(f"90 天全部一致（不一致 {mismatch} 天）", mismatch == 0)

    print("\n② ~④ 一年的分布")
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    per_day, weekday_n, weekend_n = [], [], []
    for d in range(365):
        now = start + timedelta(days=d)
        n = len(plan(now))
        per_day.append(n)
        (weekend_n if now.astimezone(tz).weekday() >= 5 else weekday_n).append(n)

    case(f"沒有零活動的日子（最少 {min(per_day)} 次/天）", min(per_day) >= 1)
    case(f"週末比平日少（平日 {statistics.mean(weekday_n):.2f}／"
         f"週末 {statistics.mean(weekend_n):.2f}）",
         statistics.mean(weekend_n) < statistics.mean(weekday_n))
    case(f"每天次數確實會變（出現過 {len(set(per_day))} 種）", len(set(per_day)) >= 3)

    day = datetime(2026, 3, 15, tzinfo=timezone.utc)
    a = humanize.todays_slots(SLOTS, RUNS, WEEKEND, TZ, "userA/repo", day)
    b = humanize.todays_slots(SLOTS, RUNS, WEEKEND, TZ, "userB/repo", day)
    case("不同 repo 的行程不同", a != b, f"{a} vs {b}")

    print("\n⑤ 呼叫節奏是叢發式的")
    ds = [humanize.next_delay(CFG) for _ in range(20000)]
    short = sum(1 for x in ds if x <= 2) / len(ds)
    long = sum(1 for x in ds if x > 60) / len(ds)
    case(f"多數是連續操作（≤2 秒佔 {short*100:.0f}%）", 0.4 <= short <= 0.7)
    case(f"偶爾長時間離開（>60 秒佔 {long*100:.1f}%）", 0.03 <= long <= 0.15)
    case(f"中位數遠小於平均（{statistics.median(ds):.1f}s vs "
         f"{statistics.mean(ds):.1f}s，代表右偏而非均勻）",
         statistics.median(ds) * 3 < statistics.mean(ds))

    disabled = dict(CFG, enabled=False)
    off = [humanize.next_delay(disabled, (1, 8)) for _ in range(2000)]
    case("明確設定不等待時完全不睡",
         humanize.next_delay(CFG, (0, 0)) == 0.0)
    case("關掉擬人化時退回 api_delay_seconds 的範圍",
         all(1 <= x <= 8 for x in off) and statistics.median(off) > 3)

    print("\n⑥ 延後清除")
    from e5keeper.apis import CATALOG
    from e5keeper.graph import GraphClient

    spec = next(s for s in CATALOG if s.id == "cal.todo.cycle")
    deletes = []

    class Fake(GraphClient):
        def request(self, method, path, *a, **k):
            if method.upper() == "DELETE":
                deletes.append(path)
                return 204, {}, "", 0.01, 1
            return 201, {"id": "X1", "displayName": "probe"}, "", 0.01, 1

    c = Fake(access_token="x", defer_cleanup=True)
    res = c.call(spec)
    case("建立當下不刪除", not deletes and "稍後清除" in res.summary, res.summary)
    case("刪除動作有被排入待辦", len(c.pending_deletes) == 1, str(c.pending_deletes))
    for path, _ in c.pending_deletes:
        c.request("DELETE", path)
    case("本輪結束時確實刪掉了", len(deletes) == 1, str(deletes))

    c2 = Fake(access_token="x", defer_cleanup=False)
    deletes.clear()
    res2 = c2.call(spec)
    case("關掉延後清除時仍是立刻刪", len(deletes) == 1 and "已清除" in res2.summary,
         res2.summary)

    print("\n" + ("✅ 擬人化測試全數通過" if not bad else f"❌ 有 {bad} 項沒通過"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
