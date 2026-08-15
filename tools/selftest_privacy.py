#!/usr/bin/env python3
"""外洩防護測試 —— 針對「公開 repo」這個前提。

公開 repo 有兩個所有人都讀得到的地方：
  · Actions 的執行日誌（而且是即時串流的，不用等執行結束）
  · commit 進 repo 的檔案（history/、STATUS.md、state/，而且永久保存）

這支測試會用一個「會外洩的假帳號」把整套流程跑一遍，
然後逐一斷言：這兩個地方都不能出現完整 email、租用戶 ID、
姓名、信箱統計、裝置碼、或任何 token。

用法：
    python tools/selftest_privacy.py
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 這些字串一個都不准出現在公開的地方
REAL_EMAIL = "administrator@contoso-real-tenant.onmicrosoft.com"
REAL_NAME = "陳大文 Da-Wen Chen"
REAL_ORG = "Contoso 實業股份有限公司"
TENANT_GUID = "aabbccdd-1122-3344-5566-778899aabbcc"
REFRESH_TOKEN = "0.AXoAREFRESHTOKEN" + "z" * 60
DEVICE_CODE = "KQPW-3M2X"

SENT: list[str] = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path
        if "/actions/secrets/public-key" in path:
            from nacl import encoding, public
            global _SK
            _SK = public.PrivateKey.generate()
            return self._send(200, {"key_id": "1",
                                    "key": _SK.public_key.encode(
                                        encoding.Base64Encoder).decode()})
        if "/directory/subscriptions" in path:
            return self._send(200, {"value": [{
                "skuPartNumber": "DEVELOPERPACK_E5", "status": "Enabled",
                "nextLifecycleDateTime": "2026-09-30T00:00:00Z"}]})
        if "$select=displayName,userPrincipalName" in path:
            return self._send(200, {"displayName": REAL_NAME,
                                    "userPrincipalName": REAL_EMAIL})
        if path.split("?")[0].endswith("/drive"):
            return self._send(200, {"quota": {"used": 44_812_345_678,
                                              "total": 1_099_511_627_776}})
        if path.split("?")[0].endswith("/mailFolders/inbox"):
            return self._send(200, {"unreadItemCount": 37, "totalItemCount": 12841})
        if "/organization" in path:
            return self._send(200, {"value": [{"displayName": REAL_ORG}]})
        # 重點：Graph 的錯誤訊息會原封不動回吐完整 email 和租用戶 ID
        if "/users" in path or "/people" in path or "/joinedTeams" in path:
            return self._send(404, {"error": {
                "code": "Request_ResourceNotFound",
                "message": (f"Resource '{REAL_EMAIL}' does not exist in tenant "
                            f"{TENANT_GUID}. Trace ID: {TENANT_GUID}"),
            }})
        return self._send(200, {"value": [{"id": "1", "displayName": REAL_NAME}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n).decode()
        if "/sendMessage" in self.path:
            SENT.append(json.loads(raw).get("text", ""))
            return self._send(200, {"ok": True})
        if "/devicecode" in self.path:
            return self._send(200, {"device_code": "DC-" + "x" * 30,
                                    "user_code": DEVICE_CODE,
                                    "verification_uri": "https://microsoft.com/devicelogin",
                                    "expires_in": 900, "interval": 0})
        if "/oauth2/v2.0/token" in self.path:
            return self._send(200, {"access_token": "ACC" + "a" * 40,
                                    "refresh_token": REFRESH_TOKEN, "expires_in": 3600})
        return self._send(201, {"id": "X", "displayName": REAL_NAME})

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if "/actions/secrets/" in self.path:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        return self._send(201, {"id": "X", "name": "probe.txt", "size": 10})

    def do_DELETE(self):
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


def _strip_mask_lines(text: str) -> str:
    """去掉 ::add-mask:: 指令行再檢查。

    這些行是「請 runner 把這個值遮蔽掉」的指令，GitHub 的 runner 會攔截並執行它，
    不會把它印進日誌 —— 這正是遮蔽機制本身的運作方式。我們這裡是直接抓 stdout，
    所以看得到；真正的 Actions 日誌裡不會有。剩下的每一行才是真的會被公開的內容。
    """
    kept, masked = [], []
    for line in text.splitlines():
        if line.startswith("::add-mask::"):
            masked.append(line[len("::add-mask::"):])
        else:
            kept.append(line)
    body = "\n".join(kept)
    # 反過來確認：被遮蔽的值，不能同時又出現在其他一般輸出行裡
    for value in masked:
        if len(value) >= 8 and value in body:
            print(f"  ⚠️ 有值同時出現在一般輸出行：{value[:12]}…")
    return body


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    from e5keeper import auth, device_auth, graph, history, notify
    from e5keeper.config import load_settings
    from e5keeper.runner import run_all

    auth.LOGIN_HOST = base
    device_auth.LOGIN_HOST = base
    graph.GRAPH_V1, graph.GRAPH_BETA = base + "/v1.0", base + "/beta"
    notify.API_ROOT = base

    tmp = Path(tempfile.mkdtemp(prefix="e5keeper-privacy-"))
    history.HISTORY_DIR = tmp / "history"
    history.STATUS_FILE = tmp / "STATUS.md"
    summary = tmp / "step_summary.md"
    summary.touch()

    os.environ.update({
        "E5_ACCOUNTS": json.dumps([{
            "alias": "E5-主帳號", "email": REAL_EMAIL, "mode": "delegated",
            "tenant": TENANT_GUID, "client_id": TENANT_GUID,
            "refresh_token": REFRESH_TOKEN}], ensure_ascii=False),
        "TELEGRAM_BOT_TOKEN": "123:FAKE",
        "TELEGRAM_CHAT_ID": "99999",
        "GITHUB_ACTIONS": "true",            # 模擬「日誌是公開的」情境
        "GITHUB_STEP_SUMMARY": str(summary),
    })
    os.environ.pop("GH_PAT", None)

    settings = load_settings()
    settings.raw["run"]["api_delay_seconds"] = [0, 0]
    settings.raw["run"]["account_delay_seconds"] = [0, 0]

    # 跑測試模式（全部 API 都跑，最容易把東西漏出來）
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rep = run_all(settings, test_mode=True, trigger="telegram:/test (my_tg_handle)")
        notify.send_report(settings, rep)
        history.record(settings, rep, push=False)
        from e5keeper.main import _job_summary
        _job_summary(settings, rep)
    public_log = _strip_mask_lines(buf.getvalue())

    committed = "\n".join(
        p.read_text(encoding="utf-8")
        for p in list(history.HISTORY_DIR.glob("*.jsonl")) + [history.STATUS_FILE]
        if p.exists()
    )
    job_summary = summary.read_text(encoding="utf-8")
    telegram = "\n".join(SENT)

    bad = 0

    def check(where: str, haystack: str, needle: str, label: str, want: bool = False):
        nonlocal bad
        found = needle in haystack
        ok = found if want else not found
        mark = "✅" if ok else "❌"
        verb = "有" if want else "沒有"
        print(f"  {mark} {where}{verb}出現{label}")
        if not ok:
            bad += 1
            idx = haystack.find(needle)
            if idx >= 0:
                print(f"      ↳ {haystack[max(0, idx - 70):idx + 70]!r}")

    print("① Actions 公開日誌")
    for label, needle in (("完整 email", REAL_EMAIL), ("真實姓名", REAL_NAME),
                          ("組織名稱", REAL_ORG), ("租用戶 ID", TENANT_GUID),
                          ("refresh token", REFRESH_TOKEN),
                          ("信箱統計", "12841"), ("OneDrive 用量", "41.7")):
        check("日誌", public_log, needle, label)

    print("\n② commit 進 repo 的檔案（history/、STATUS.md）")
    for label, needle in (("完整 email", REAL_EMAIL), ("真實姓名", REAL_NAME),
                          ("租用戶 ID", TENANT_GUID), ("refresh token", REFRESH_TOKEN),
                          ("Telegram 使用者名稱", "my_tg_handle")):
        check("檔案", committed, needle, label)
    # history_detail=minimal（預設）：連遮蔽版 email 都不該出現 ——
    # 遮蔽版仍保留完整網域，光靠網域就查得出租用戶 ID
    check("檔案", committed, "contoso-real-tenant.onmicrosoft.com", "email 網域")
    check("檔案", committed, "DEVELOPERPACK_E5", "訂閱 SKU 名稱")
    check("檔案", committed, '"days_left"', "訂閱剩餘天數")
    check("檔案", committed, '"alias": "E5-主帳號"', "帳號別名（這個應該留著）", want=True)
    check("檔案", committed, '"ok":', "成功數統計（這個應該留著）", want=True)

    print("\n③ Actions Job Summary（執行頁面上，公開）")
    for label, needle in (("完整 email", REAL_EMAIL), ("租用戶 ID", TENANT_GUID)):
        check("Job Summary", job_summary, needle, label)

    print("\n④ Telegram 私訊（這裡反而『應該』看得到細節，才好除錯）")
    check("Telegram", telegram, REAL_NAME, "真實姓名", want=True)
    check("Telegram", telegram, REFRESH_TOKEN, "refresh token")

    print("\n⑤ 裝置碼授權：代碼不得進入公開日誌")
    SENT.clear()
    os.environ["GH_PAT"] = "ghp_fake"
    os.environ["GITHUB_REPOSITORY"] = "user/repo"
    from e5keeper import ghsecrets
    ghsecrets.API = base
    settings = load_settings()
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        device_auth.POLL_GRACE = 0
        device_auth.authorize(settings, alias="x", tenant="common", client_id="cid")
    auth_log = _strip_mask_lines(buf2.getvalue())
    check("授權日誌", auth_log, DEVICE_CODE, "裝置碼")
    check("Telegram", "\n".join(SENT), DEVICE_CODE, "裝置碼", want=True)

    print("\n⑥ 預期帳號不符時必須中止")
    SENT.clear()
    with contextlib.redirect_stdout(io.StringIO()):
        rc = device_auth.authorize(settings, alias="x", tenant="common",
                                   client_id="cid", expected_upn="someone-else@other.com")
    ok = rc != 0 and any("被拒絕" in m for m in SENT)
    print(f"  {'✅' if ok else '❌'} 登入帳號與預期不符時中止且不寫入")
    if not ok:
        bad += 1

    print("\n" + ("✅ 外洩防護測試全數通過" if not bad else f"❌ 有 {bad} 項外洩！"))
    server.shutdown()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
