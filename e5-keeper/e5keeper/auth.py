"""取得 Microsoft Graph access token。

支援兩種模式：
  delegated → refresh_token 換 access_token（並接住輪換後的新 refresh_token）
  app       → client_credentials 直接換 access_token
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

from .config import Account
from .utils import gh_add_mask, log, mask_secret

LOGIN_HOST = "https://login.microsoftonline.com"
GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"

# 這些 error code 代表 token 是真的死了，重試沒有意義，必須人工重新授權
FATAL_ERRORS = {
    "invalid_grant",
    "interaction_required",
    "consent_required",
    "unauthorized_client",
    "invalid_client",
}


class TokenError(Exception):
    """取得 token 失敗。fatal=True 代表需要人工重新授權。"""

    def __init__(self, message: str, *, fatal: bool = False, code: str = ""):
        super().__init__(message)
        self.fatal = fatal
        self.code = code


@dataclass
class TokenResult:
    access_token: str
    expires_in: int = 3600
    new_refresh_token: str = ""   # 有值代表 Microsoft 輪換了 refresh token
    rotated: bool = False
    scopes: str = ""


def _token_endpoint(tenant: str) -> str:
    return f"{LOGIN_HOST}/{tenant or 'common'}/oauth2/v2.0/token"


def _post_token(url: str, data: dict, timeout: int) -> dict:
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = requests.post(
                url,
                data=data,
                timeout=timeout,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise TokenError(f"連線到登入端點失敗：{exc}") from exc

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError as exc:
                # 200 但不是 JSON —— 通常是中間有代理伺服器或攔截頁面
                last_exc = exc
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise TokenError(
                    "登入端點回傳的不是 JSON（可能被網路中介攔截）："
                    f"{resp.text[:200]}"
                ) from exc

        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        code = str(payload.get("error", "")) or f"HTTP {resp.status_code}"
        desc = str(payload.get("error_description", resp.text))[:400]
        fatal = code in FATAL_ERRORS

        # 暫時性錯誤才重試
        if not fatal and resp.status_code >= 500 and attempt < 2:
            time.sleep(2 ** attempt)
            continue

        hint = ""
        if code == "invalid_grant":
            hint = "（refresh token 已失效或被撤銷，請用 tools/get_token.py 重新授權）"
        elif code == "invalid_client":
            hint = "（client_id 或 client_secret 不對，或 secret 已過期）"
        raise TokenError(f"{code}: {desc}{hint}", fatal=fatal, code=code)

    raise TokenError(f"取得 token 失敗：{last_exc}")


def acquire_token(account: Account, timeout: int = 30) -> TokenResult:
    """替一個帳號取得 access token。"""
    if account.mode == "app":
        return _acquire_app(account, timeout)
    return _acquire_delegated(account, timeout)


def _acquire_delegated(account: Account, timeout: int) -> TokenResult:
    scope = " ".join(account.scopes) if account.scopes else "offline_access " + GRAPH_DEFAULT_SCOPE
    data = {
        "client_id": account.client_id,
        "grant_type": "refresh_token",
        "refresh_token": account.refresh_token,
        "scope": scope,
    }
    if account.client_secret:
        data["client_secret"] = account.client_secret

    payload = _post_token(_token_endpoint(account.tenant), data, timeout)

    access = payload.get("access_token", "")
    if not access:
        raise TokenError("登入端點沒有回傳 access_token")
    new_refresh = payload.get("refresh_token", "") or ""
    rotated = bool(new_refresh and new_refresh != account.refresh_token)

    gh_add_mask(access, new_refresh)
    log(
        f"取得 access token（委派）{mask_secret(access)}"
        + ("，refresh token 已輪換" if rotated else ""),
        level="ok",
    )
    return TokenResult(
        access_token=access,
        expires_in=int(payload.get("expires_in", 3600)),
        new_refresh_token=new_refresh,
        rotated=rotated,
        scopes=payload.get("scope", ""),
    )


def _acquire_app(account: Account, timeout: int) -> TokenResult:
    data = {
        "client_id": account.client_id,
        "client_secret": account.client_secret,
        "grant_type": "client_credentials",
        "scope": GRAPH_DEFAULT_SCOPE,
    }
    payload = _post_token(_token_endpoint(account.tenant), data, timeout)
    access = payload.get("access_token", "")
    if not access:
        raise TokenError("登入端點沒有回傳 access_token")
    gh_add_mask(access)
    log(f"取得 access token（應用程式）{mask_secret(access)}", level="ok")
    return TokenResult(
        access_token=access,
        expires_in=int(payload.get("expires_in", 3600)),
        scopes=payload.get("scope", ""),
    )
