#!/usr/bin/env python3
"""E5 保活精靈 · 授權小工具

在你自己的電腦上執行，取得可以貼進 GitHub Secret 的帳號 JSON。

用法：
    python tools/get_token.py                    # 裝置碼流程（預設，推薦）
    python tools/get_token.py --paste            # 手動貼一組現成的 refresh_token 來驗證
    python tools/get_token.py --app              # 建立應用程式權限（client_credentials）的設定
    python tools/get_token.py --append accounts.json   # 把結果併進既有的帳號檔

只需要 requests：
    pip install requests
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    sys.exit("請先安裝 requests：pip install requests")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from e5keeper.config import DEFAULT_SCOPES, FALLBACK_CLIENT_ID
except Exception:  # noqa: BLE001 — 讓這支工具即使被單獨複製出去也能跑
    FALLBACK_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"
    DEFAULT_SCOPES = [
        "offline_access", "openid", "profile", "User.Read", "User.ReadBasic.All",
        "Mail.ReadWrite", "Mail.Send", "MailboxSettings.Read", "Files.ReadWrite.All",
        "Calendars.ReadWrite", "Contacts.ReadWrite", "Tasks.ReadWrite", "Notes.Read",
        "People.Read", "Sites.Read.All", "Team.ReadBasic.All", "Directory.Read.All",
    ]

LOGIN = "https://login.microsoftonline.com"
GRAPH = "https://graph.microsoft.com/v1.0"

C = {
    "b": "\033[1m", "dim": "\033[2m", "g": "\033[32m", "y": "\033[33m",
    "r": "\033[31m", "c": "\033[36m", "x": "\033[0m",
}


def say(msg: str = "", color: str = "") -> None:
    print(f"{C.get(color, '')}{msg}{C['x']}" if color else msg)


def rule(title: str = "") -> None:
    say()
    say("─" * 60, "dim")
    if title:
        say(f"  {title}", "b")
        say("─" * 60, "dim")


def ask(prompt: str, default: str = "") -> str:
    suffix = f" {C['dim']}[{default}]{C['x']}" if default else ""
    value = input(f"{C['c']}?{C['x']} {prompt}{suffix}: ").strip()
    return value or default


# ══════════════════ 裝置碼流程 ══════════════════

def device_code_flow(tenant: str, client_id: str, scopes: list[str]) -> dict:
    scope = " ".join(scopes)
    rule("步驟 1／2　向 Microsoft 要一組裝置碼")

    resp = requests.post(
        f"{LOGIN}/{tenant}/oauth2/v2.0/devicecode",
        data={"client_id": client_id, "scope": scope},
        timeout=30,
    )
    if not resp.ok:
        payload = _json(resp)
        code = payload.get("error", "")
        say(f"✗ 取得裝置碼失敗：{code}", "r")
        say(f"  {payload.get('error_description', resp.text)[:400]}", "dim")
        if code in ("unauthorized_client", "invalid_client", "invalid_request"):
            say()
            say("常見原因：Azure 應用程式沒有開啟「允許公用用戶端流程」。", "y")
            say("  → Azure 入口網站 → 應用程式註冊 → 你的應用 → 驗證 →", "y")
            say("     最下方「允許公用用戶端流程」請選【是】，儲存後再試一次。", "y")
        sys.exit(1)

    data = resp.json()
    rule("步驟 2／2　到瀏覽器完成登入")
    say()
    say(f"  1. 開啟網址：{C['b']}{data['verification_uri']}{C['x']}")
    say(f"  2. 輸入代碼：{C['b']}{C['g']}{data['user_code']}{C['x']}")
    say(f"  3. 用你的 {C['b']}E5 帳號{C['x']} 登入並同意授權")
    say()
    say(f"  （代碼 {data.get('expires_in', 900) // 60} 分鐘內有效）", "dim")
    say()

    interval = int(data.get("interval", 5))
    deadline = time.time() + int(data.get("expires_in", 900))
    dots = 0

    while time.time() < deadline:
        time.sleep(interval)
        token_resp = requests.post(
            f"{LOGIN}/{tenant}/oauth2/v2.0/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": client_id,
                "device_code": data["device_code"],
            },
            timeout=30,
        )
        if token_resp.ok:
            print()
            say("✓ 授權成功！", "g")
            return token_resp.json()

        err = _json(token_resp).get("error", "")
        if err == "authorization_pending":
            dots = (dots + 1) % 4
            print(f"\r  等待你在瀏覽器完成授權{'.' * dots}   ", end="", flush=True)
            continue
        if err == "slow_down":
            interval += 5
            continue
        print()
        if err == "expired_token":
            say("✗ 代碼已過期，請重新執行這支工具。", "r")
        elif err == "authorization_declined":
            say("✗ 你在瀏覽器上拒絕了授權。", "r")
        else:
            say(f"✗ 授權失敗：{err}", "r")
            say(f"  {_json(token_resp).get('error_description', '')[:400]}", "dim")
        sys.exit(1)

    print()
    say("✗ 等待逾時。", "r")
    sys.exit(1)


# ══════════════════ 其他流程 ══════════════════

def refresh_flow(tenant: str, client_id: str, client_secret: str,
                 refresh_token: str, scopes: list[str]) -> dict:
    data = {
        "client_id": client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "scope": " ".join(scopes),
    }
    if client_secret:
        data["client_secret"] = client_secret
    resp = requests.post(f"{LOGIN}/{tenant}/oauth2/v2.0/token", data=data, timeout=30)
    if not resp.ok:
        payload = _json(resp)
        say(f"✗ refresh token 無效：{payload.get('error', '')}", "r")
        say(f"  {payload.get('error_description', '')[:400]}", "dim")
        sys.exit(1)
    say("✓ refresh token 有效。", "g")
    return resp.json()


def client_credentials_flow(tenant: str, client_id: str, client_secret: str) -> dict:
    resp = requests.post(
        f"{LOGIN}/{tenant}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    if not resp.ok:
        payload = _json(resp)
        say(f"✗ 取得 token 失敗：{payload.get('error', '')}", "r")
        say(f"  {payload.get('error_description', '')[:400]}", "dim")
        sys.exit(1)
    say("✓ 應用程式權限驗證成功。", "g")
    return resp.json()


# ══════════════════ 驗證 ══════════════════

def whoami(access_token: str, user_path: str = "/me") -> dict:
    resp = requests.get(
        f"{GRAPH}{user_path}?$select=displayName,userPrincipalName,mail,id",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if not resp.ok:
        say(f"⚠ 查詢使用者資料失敗（HTTP {resp.status_code}），"
            f"可能是權限不足，但 token 本身沒問題。", "y")
        return {}
    return resp.json()


def probe(access_token: str, user_path: str = "/me") -> None:
    """快速確認幾個關鍵權限有沒有拿到。"""
    checks = [
        ("郵件", f"{user_path}/messages?$top=1&$select=id"),
        ("雲端硬碟", f"{user_path}/drive"),
        ("行事曆", f"{user_path}/events?$top=1&$select=id"),
        ("目錄", "/organization"),
    ]
    rule("權限快檢")
    for name, path in checks:
        try:
            r = requests.get(
                f"{GRAPH}{path}",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=20,
            )
            mark, color = ("✓", "g") if r.ok else ("✗", "y")
            say(f"  {mark} {name:<6} HTTP {r.status_code}", color)
        except requests.RequestException as exc:
            say(f"  ✗ {name:<6} {exc.__class__.__name__}", "y")


# ══════════════════ 輸出 ══════════════════

def emit(entry: dict, append_path: str | None) -> None:
    rule("完成！把下面這段貼進 GitHub Secret")
    say()
    say("Secret 名稱： E5_ACCOUNTS", "b")
    say("（repo → Settings → Secrets and variables → Actions → New repository secret）", "dim")
    say()

    entries = [entry]
    if append_path:
        path = Path(append_path)
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    existing = [existing]
                existing = [x for x in existing if x.get("email") != entry.get("email")]
                entries = existing + [entry]
                say(f"已併入 {path}（原有 {len(existing)} 個帳號）", "dim")
            except Exception as exc:  # noqa: BLE001
                say(f"⚠ 讀取 {path} 失敗：{exc}，改成只輸出這一個帳號", "y")

    text = json.dumps(entries, ensure_ascii=False, indent=2)
    say()
    say(text, "c")
    say()

    out = Path(append_path) if append_path else Path("accounts.json")
    try:
        out.write_text(text + "\n", encoding="utf-8")
        say(f"↳ 也存了一份到 {out.resolve()}", "dim")
        say("  ⚠️ 這個檔案含有金鑰，貼完請刪掉，千萬不要 commit（.gitignore 已擋）", "y")
    except OSError as exc:
        say(f"（寫入檔案失敗：{exc}）", "dim")


def _json(resp) -> dict:
    try:
        return resp.json()
    except ValueError:
        return {}


# ══════════════════ 主流程 ══════════════════

def main() -> None:
    p = argparse.ArgumentParser(description="E5 保活精靈授權工具")
    p.add_argument("--paste", action="store_true", help="手動貼一組現成的 refresh_token 來驗證")
    p.add_argument("--app", action="store_true", help="建立應用程式權限（client_credentials）設定")
    p.add_argument("--append", metavar="FILE", help="把結果併進既有的帳號 JSON 檔")
    p.add_argument("--alias", default="", help="這個帳號的顯示別名")
    p.add_argument("--tenant", default="", help="租用戶：common 或你的網域 / 租用戶 ID")
    p.add_argument("--client-id", default="", help="Azure 應用程式的用戶端 ID")
    args = p.parse_args()

    say()
    say("╔══════════════════════════════════════════════════════╗", "c")
    say("║        E5 保活精靈 · 授權工具                        ║", "c")
    say("╚══════════════════════════════════════════════════════╝", "c")

    mode = "app" if args.app else "delegated"
    default_tenant = "common" if mode == "delegated" else ""

    rule("基本資訊")
    tenant = args.tenant or ask("租用戶（個人自建的 E5 直接按 Enter 用 common）", default_tenant)
    while mode == "app" and tenant in ("", "common", "organizations"):
        say("應用程式權限必須填實際的租用戶 ID 或網域，不能用 common。", "y")
        tenant = ask("租用戶 ID 或網域（例如 contoso.onmicrosoft.com）")

    client_id = args.client_id or ask("用戶端 ID（Application ID）", FALLBACK_CLIENT_ID)
    if client_id == FALLBACK_CLIENT_ID and mode == "delegated":
        say("  ↳ 用的是 Azure PowerShell 的公用 client id，可以先測試；", "dim")
        say("    正式使用建議自己註冊一個應用程式（README 有步驟）。", "dim")

    alias = args.alias or ask("這個帳號的別名（通知裡會顯示）", "E5-主帳號")

    # ── 依模式取得 token ──
    if mode == "app":
        secret = ask("用戶端密碼（Client secret 的 Value）")
        if not secret:
            sys.exit("必須提供 client secret。")
        target = ask("要操作的使用者 UPN（例如 admin@xxx.onmicrosoft.com）")
        token = client_credentials_flow(tenant, client_id, secret)
        user_path = f"/users/{target}"
        entry = {
            "alias": alias, "email": target, "mode": "app", "tenant": tenant,
            "client_id": client_id, "client_secret": secret, "target_user": target,
        }
    else:
        secret = ""
        if args.paste:
            rule("驗證現有的 refresh token")
            secret = ask("用戶端密碼（公用用戶端請直接按 Enter 留空）", "")
            existing = ask("貼上 refresh_token")
            if not existing:
                sys.exit("沒有輸入 refresh token。")
            token = refresh_flow(tenant, client_id, secret, existing, DEFAULT_SCOPES)
        else:
            token = device_code_flow(tenant, client_id, DEFAULT_SCOPES)

        refresh_token = token.get("refresh_token", "")
        if not refresh_token:
            say("✗ 回應裡沒有 refresh_token。請確認 scope 有包含 offline_access。", "r")
            sys.exit(1)
        user_path = "/me"
        entry = {
            "alias": alias, "email": "", "mode": "delegated", "tenant": tenant,
            "client_id": client_id, "refresh_token": refresh_token,
        }
        if secret:
            entry["client_secret"] = secret

    # ── 驗證身分 ──
    rule("確認帳號")
    me = whoami(token["access_token"], user_path)
    upn = me.get("userPrincipalName") or me.get("mail") or entry.get("email", "")
    if upn:
        entry["email"] = upn
        if mode == "app":
            entry["target_user"] = upn
        say(f"  登入身分：{C['b']}{me.get('displayName', '?')}{C['x']}  <{upn}>")
    elif not entry.get("email"):
        entry["email"] = ask("自動查詢不到帳號，請手動輸入這個帳號的 email")

    probe(token["access_token"], user_path)
    emit(entry, args.append)

    rule("接下來")
    say("  1. 把上面的 JSON 存成 GitHub Secret：E5_ACCOUNTS")
    say("  2. 另外建立 TELEGRAM_BOT_TOKEN、TELEGRAM_CHAT_ID、GH_PAT 三個 Secret")
    say("  3. 到 Actions 頁面手動跑一次「🧪 E5 測試模式」確認一切正常")
    say()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\n已取消。", "y")
        sys.exit(130)
