#!/usr/bin/env python3
"""離線自我測試 —— 不碰真實帳號，也不需要網路。

會在本機起一個假的 Microsoft / Telegram 伺服器，把整條流程完整跑一遍
（取 token → 呼叫 API → 重試 → 組通知 → 寫歷史 → 週報），
然後把「本來會送到 Telegram 的訊息」直接印出來給你看。

用途：
  · 改完程式碼想確認沒改壞
  · 想先看看通知長什麼樣子再決定要不要調 config.yml

用法：
    python tools/selftest.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SENT: list[str] = []
_retry_seen: dict[str, int] = {}


# ══════════════════ 假伺服器 ══════════════════

class Handler(BaseHTTPRequestHandler):
    fail_token = False       # 設成 True 就模擬 refresh token 失效

    def log_message(self, *_args):
        pass

    # ── 共用 ──
    def _send(self, code: int, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode("utf-8") if length else ""

    # ── 路由 ──
    def do_GET(self):
        if "/getUpdates" in self.path:
            return self._send(200, {"ok": True, "result": []})
        return self._graph("GET")

    def do_POST(self):
        raw = self._read_body()
        if "/oauth2/v2.0/token" in self.path:
            if Handler.fail_token:
                return self._send(400, {
                    "error": "invalid_grant",
                    "error_description": "AADSTS700082: The refresh token has expired due to inactivity.",
                })
            return self._send(200, {
                "access_token": "FAKE_ACCESS_TOKEN_" + "x" * 40,
                "refresh_token": "FAKE_REFRESH_TOKEN_ROTATED_" + "y" * 40,
                "expires_in": 3600,
                "scope": "User.Read Mail.ReadWrite",
            })
        if "/sendMessage" in self.path:
            try:
                SENT.append(json.loads(raw).get("text", ""))
            except json.JSONDecodeError:
                SENT.append(raw)
            return self._send(200, {"ok": True, "result": {"message_id": len(SENT)}})
        return self._graph("POST")

    def do_PUT(self):
        self._read_body()
        return self._graph("PUT")

    def do_DELETE(self):
        return self._graph("DELETE")

    # ── 假的 Graph ──
    def _graph(self, method: str):
        path = self.path

        if method == "DELETE":
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # 刻意製造各種狀況，確認顯示與重試邏輯都對
        if "authentication/methods" in path:
            return self._send(403, {"error": {
                "code": "Authorization_RequestDenied",
                "message": "Insufficient privileges to complete the operation.",
            }})
        if "/photo" in path:
            return self._send(404, {"error": {
                "code": "ImageNotFound", "message": "The photo wasn't found.",
            }})
        if "/people" in path:
            return self._send(503, {"error": {
                "code": "ServiceUnavailable", "message": "Service is temporarily unavailable.",
            }})
        if "getOffice365ActiveUserDetail" in path:
            seen = _retry_seen.get("report", 0)
            _retry_seen["report"] = seen + 1
            if seen == 0:   # 第一次故意 429，驗證指數退避
                self.send_response(429)
                self.send_header("Retry-After", "1")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = b"Report Refresh Date,User Principal Name\n2026-08-14,a@b.com\n" * 20
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if "/directory/subscriptions" in path:
            expiry = datetime.now(timezone.utc) + timedelta(days=12)
            return self._send(200, {"value": [{
                "skuPartNumber": "DEVELOPERPACK_E5",
                "status": "Enabled",
                "totalLicenses": 25,
                "nextLifecycleDateTime": expiry.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }]})

        if "/sendMail" in path:
            return self._send(202, {})

        if re.search(r"/drive$", path.split("?")[0]):
            return self._send(200, {
                "driveType": "business",
                "quota": {"used": 3_221_225_472, "total": 1_099_511_627_776},
            })
        if "/mailFolders/inbox?" in path or path.split("?")[0].endswith("/mailFolders/inbox"):
            return self._send(200, {"unreadItemCount": 7, "totalItemCount": 1284})
        if "$select=displayName,userPrincipalName,mail,id" in path:
            return self._send(200, {
                "displayName": "測試使用者", "userPrincipalName": "demo@contoso.onmicrosoft.com",
            })
        if method in ("POST", "PUT"):
            return self._send(201, {
                "id": "ITEM-" + str(len(SENT) + 1),
                "name": "notes-2026-08-15-x19q.md",
                "subject": "Notes 2026-08-15 (8nd9)",
                "displayName": "Alex Km2p4t",
                "size": 614,
            })

        return self._send(200, {"value": [{"id": f"x{i}", "displayName": f"項目 {i}",
                                           "subject": f"郵件 {i}"} for i in range(6)]})


def start_server() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


# ══════════════════ 測試主體 ══════════════════

def main() -> int:
    server, base = start_server()
    print(f"假伺服器啟動於 {base}\n")

    from e5keeper import auth, graph, history, notify, report as report_mod
    from e5keeper.config import load_settings

    auth.LOGIN_HOST = base
    graph.GRAPH_V1 = base + "/v1.0"
    graph.GRAPH_BETA = base + "/beta"
    notify.API_ROOT = base

    tmp = Path(tempfile.mkdtemp(prefix="e5keeper-selftest-"))
    history.HISTORY_DIR = tmp / "history"
    history.STATUS_FILE = tmp / "STATUS.md"

    os.environ["E5_ACCOUNTS"] = json.dumps([
        {"alias": "E5-主帳號", "email": "demo@contoso.onmicrosoft.com",
         "mode": "delegated", "tenant": "common", "client_id": "fake-client-id",
         "refresh_token": "FAKE_REFRESH_TOKEN_" + "z" * 40},
        {"alias": "E5-備援", "email": "backup@contoso.onmicrosoft.com",
         "mode": "app", "tenant": "contoso.onmicrosoft.com",
         "client_id": "fake-app-id", "client_secret": "fake-secret",
         "target_user": "backup@contoso.onmicrosoft.com"},
    ], ensure_ascii=False)
    os.environ["TELEGRAM_BOT_TOKEN"] = "123456:FAKE"
    os.environ["TELEGRAM_CHAT_ID"] = "99999"
    os.environ.pop("GH_PAT", None)
    os.environ.pop("GITHUB_ACTIONS", None)

    settings = load_settings()
    settings.raw["run"]["api_delay_seconds"] = [0, 0]
    settings.raw["run"]["account_delay_seconds"] = [0, 0]
    settings.raw["run"]["retry"]["initial_backoff"] = 0.2
    # 排程模式下訂閱查詢是機率性的（config 預設 40%）。這裡要驗證的是
    # 「查到之後有沒有正確顯示」，不是機率本身，所以強制必查讓測試穩定。
    # 機率邏輯本身由 selftest_humanize.py 負責。
    settings.raw["humanize"]["subscription_check_probability"] = 1.0

    from e5keeper.runner import run_all

    failures = 0

    # ── 情境 1：排程執行 ──
    banner("情境 1／3　排程執行（隨機抽 API、兩個帳號）")
    rep = run_all(settings, test_mode=False, trigger="cron")
    notify.send_report(settings, rep)
    history.record(settings, rep, push=False)
    failures += check(rep, expect_min_apis=8, expect_rotation=True)

    # ── 情境 2：測試模式 ──
    banner("情境 2／3　測試模式（單一帳號、全部 API）")
    SENT.clear()
    rep2 = run_all(settings, test_mode=True, accounts=[settings.accounts[0]],
                   trigger="telegram:/test (demo)")
    notify.send_report(settings, rep2)
    history.record(settings, rep2, push=False)
    failures += check(rep2, expect_min_apis=25, expect_warn=True)

    # ── 情境 3：token 失效 ──
    banner("情境 3／4　token 失效（模擬 invalid_grant）")
    SENT.clear()
    Handler.fail_token = True
    rep3 = run_all(settings, test_mode=False, trigger="cron")
    notify.send_report(settings, rep3)
    Handler.fail_token = False
    show_messages("token 失效通知")
    if not any("重新授權" in m for m in SENT):
        print("  ❌ token 失效的通知裡沒有出現重新授權指引")
        failures += 1
    if all(a.token_ok for a in rep3.accounts):
        print("  ❌ 預期 token 取得失敗，但全部都成功了")
        failures += 1

    # ── 情境 4：週報 ──
    banner("情境 4／5　每週統計報告")
    SENT.clear()
    report_mod.weekly(settings, days=7)
    show_messages("週報")

    # ── 情境 5：邊界情況回歸測試 ──
    banner("情境 5／5　邊界情況回歸測試")
    failures += regressions(settings)

    # ── 結果 ──
    banner("驗證結果")
    status = Path(history.STATUS_FILE)
    print(f"STATUS.md 已產生：{status.exists()}（{status}）")
    print(f"歷史紀錄檔：{sorted(p.name for p in history.HISTORY_DIR.glob('*.jsonl'))}")
    print()
    if failures:
        print(f"❌ 有 {failures} 項檢查沒通過")
    else:
        print("✅ 全部檢查通過")
    server.shutdown()
    return 1 if failures else 0


def regressions(settings) -> int:
    """針對過去修掉的具體問題做定點檢查，避免改壞了沒人發現。"""
    from e5keeper import notify
    from e5keeper.graph import ApiResult, GraphClient
    from e5keeper.runner import AccountRun, RunReport, Subscription

    bad = 0

    def case(name: str, ok: bool, detail: str = "") -> None:
        nonlocal bad
        print(f"  {'✅' if ok else '❌'} {name}" + (f"　→ {detail}" if not ok and detail else ""))
        if not ok:
            bad += 1

    # ① 訊息長度：超長明細 + 執行標頭 + 日誌連結 + token 回寫說明，都不能超過 4096
    os.environ["GITHUB_REPOSITORY"] = "user/e5-keeper"
    os.environ["GITHUB_RUN_ID"] = "1234567890"
    run = AccountRun(alias="長訊息測試", email="verylongaddress@contoso.onmicrosoft.com",
                     mode="delegated", token_ok=True, rotated=True, index=1, total=1)
    run.subscription = Subscription(sku="DEVELOPERPACK_E5", days_left=5, next_date="2026-08-20")
    for i in range(90):
        r = ApiResult(spec_id=f"cat.some.very.long.api.id.{i:03d}", name="測試", category="mail")
        r.status, r.ok, r.elapsed = 500, False, 1.234
        r.error = "ServiceUnavailable: " + "很長的錯誤訊息內容 " * 6
        run.results.append(r)
    rep = RunReport(mode="test", trigger="regression")
    rep.accounts = [run]
    rep.secret_note = "⚠️ 長訊息測試 的 refresh token 已輪換，但<b>回寫失敗</b>：" + "原因說明 " * 20
    msgs = notify.build_messages(settings, rep)
    longest = max(len(m) for m in msgs)
    case(f"訊息長度不超過 Telegram 4096 上限（最長 {longest}）", longest <= 4096)
    case("<pre> 標籤全部成對",
         all(m.count("<pre>") == m.count("</pre>") for m in msgs))
    os.environ.pop("GITHUB_REPOSITORY", None)
    os.environ.pop("GITHUB_RUN_ID", None)

    # ② 寫入型 API 的 tolerate 設定要生效（例如沒有 Tasks 權限時的 403）
    from e5keeper import apis

    spec = next(s for s in apis.CATALOG if s.id == "cal.todo.cycle")

    class Fake403(GraphClient):
        def request(self, *a, **k):
            return 403, {"error": {"code": "Authorization_RequestDenied", "message": "no"}}, \
                   "Authorization_RequestDenied: no", 0.01, 1

    res = Fake403(access_token="x").call(spec)
    case("寫入型 API 的 403 顯示 ⚠️ 而非 ❌",
         res.tolerated and not res.ok and res.icon == "⚠️", f"icon={res.icon}")

    # ③ 建立成功但清除失敗，不能報成完全成功
    class FakeNoDelete(GraphClient):
        def request(self, method, *a, **k):
            if method.upper() == "DELETE":
                return 409, {}, "Conflict: item is locked", 0.01, 1
            return 201, {"id": "X1", "displayName": "probe"}, "", 0.01, 1

    res2 = FakeNoDelete(access_token="x").call(spec)
    case("清除失敗時不報 ✅（會留下垃圾）",
         not res2.ok and "清除失敗" in (res2.error + res2.summary), f"icon={res2.icon}")

    # ④ cleanup_after_write=False 時就真的不刪
    deletes: list[str] = []

    class FakeTrack(GraphClient):
        def request(self, method, path, *a, **k):
            if method.upper() == "DELETE":
                deletes.append(path)
            return 201, {"id": "X1", "displayName": "probe"}, "", 0.01, 1

    FakeTrack(access_token="x", cleanup_after_write=False).call(spec)
    case("cleanup_after_write=false 時不執行刪除", not deletes, f"仍刪了 {deletes}")

    # ⑤ 登入端點回傳非 JSON 時要變成 TokenError，不能炸掉整輪
    from e5keeper import auth

    class FakeResp:
        status_code, text = 200, "<html>captive portal</html>"
        headers: dict = {}

        def json(self):
            raise ValueError("not json")

    orig_post = auth.requests.post
    auth.requests.post = lambda *a, **k: FakeResp()
    try:
        auth.acquire_token(settings.accounts[0], timeout=1)
        case("登入端點回傳非 JSON 時丟出 TokenError", False, "沒有丟出例外")
    except auth.TokenError:
        case("登入端點回傳非 JSON 時丟出 TokenError", True)
    except Exception as exc:  # noqa: BLE001
        case("登入端點回傳非 JSON 時丟出 TokenError", False, f"丟出 {exc.__class__.__name__}")
    finally:
        auth.requests.post = orig_post

    # ⑥ 全部帳號都登入失敗時，STATUS.md 不能只寫「有警告」
    from e5keeper import history as hist

    dead = RunReport(mode="schedule", trigger="regression")
    dead.accounts = [AccountRun(alias="死掉的", email="a@b.com", mode="delegated",
                                token_ok=False, token_error="invalid_grant")]
    hist._write_status(settings, dead)
    text = Path(hist.STATUS_FILE).read_text(encoding="utf-8")
    case("全帳號登入失敗時 STATUS.md 顯示為嚴重狀態", "🚫" in text)

    return bad


def banner(title: str) -> None:
    print("\n" + "═" * 68)
    print(f"  {title}")
    print("═" * 68)


def check(rep, expect_min_apis: int, expect_rotation: bool = False,
          expect_warn: bool = False) -> int:
    show_messages(f"{rep.mode} 通知")
    problems = []
    ok, warn, fail, total = rep.totals
    if total < expect_min_apis:
        problems.append(f"API 數量 {total} 少於預期的 {expect_min_apis}")
    if not any(a.token_ok for a in rep.accounts):
        problems.append("沒有任何帳號成功取得 token")
    if expect_rotation and not any(a.rotated for a in rep.accounts):
        problems.append("預期委派帳號的 refresh token 會輪換，但沒有")
    if not any(a.subscription and a.subscription.days_left is not None for a in rep.accounts):
        problems.append("沒有抓到訂閱到期資訊")
    if expect_warn and warn == 0:
        problems.append("預期會有 ⚠️（403/404 之類的可容忍狀態），但一個都沒有")
    if not SENT:
        problems.append("沒有送出任何 Telegram 訊息")
    for msg in SENT:
        if len(msg) > 4096:
            problems.append(f"有訊息超過 Telegram 4096 字上限（{len(msg)}）")
        if msg.count("<pre>") != msg.count("</pre>"):
            problems.append("HTML <pre> 標籤沒有成對")
    for p in problems:
        print(f"  ❌ {p}")
    return len(problems)


def show_messages(label: str) -> None:
    print(f"\n┌─ 送出的 Telegram 訊息（{label}）：共 {len(SENT)} 則 " + "─" * 12)
    for i, msg in enumerate(SENT, 1):
        print(f"│ 第 {i} 則（{len(msg)} 字）")
        for line in msg.splitlines():
            print("│   " + line)
        print("│")
    print("└" + "─" * 60)


if __name__ == "__main__":
    sys.exit(main())
