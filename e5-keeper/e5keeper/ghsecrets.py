"""把輪換後的 refresh token 加密回寫到 GitHub Actions Secrets。

GitHub 要求 secret 用 repo 的公鑰做 libsodium sealed box 加密後才能上傳，
所以需要 PyNaCl。回寫用的 PAT 至少要有 repo secrets 的寫入權限。

注意：這一輪回寫的新值，要「下一次」workflow 執行時才讀得到，這是 GitHub 的設計。
"""

from __future__ import annotations

import base64
import json

import requests

from .config import Account, Settings
from .utils import log

API = "https://api.github.com"
SECRET_NAME = "E5_ACCOUNTS"


class SecretWriteError(Exception):
    pass


def _headers(pat: str) -> dict:
    return {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _encrypt(public_key_b64: str, value: str) -> str:
    try:
        from nacl import encoding, public
    except ImportError as exc:  # pragma: no cover
        raise SecretWriteError(
            "缺少 PyNaCl，無法加密 secret。請確認 requirements.txt 已安裝完成。"
        ) from exc

    pk = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(pk).encrypt(value.encode("utf-8"))
    return base64.b64encode(sealed).decode("utf-8")


def save_accounts(settings: Settings, accounts: list[Account]) -> tuple[bool, str]:
    """把目前（含新 refresh token 的）帳號清單寫回 E5_ACCOUNTS。

    回傳 (是否成功, 說明文字)。
    """
    if not settings.can_write_secrets:
        return False, "未提供 GH_PAT，略過回寫（token 仍在有效期內，但下次可能失效）"

    payload = json.dumps(
        [a.to_secret_dict() for a in accounts],
        ensure_ascii=False,
        separators=(",", ":"),
    )

    try:
        key_resp = requests.get(
            f"{API}/repos/{settings.repo}/actions/secrets/public-key",
            headers=_headers(settings.gh_pat),
            timeout=30,
        )
        if key_resp.status_code == 403:
            return False, "PAT 權限不足（需要 Secrets 的寫入權限）"
        if key_resp.status_code == 404:
            return False, f"找不到 repo {settings.repo}，或 PAT 沒有存取權"
        key_resp.raise_for_status()
        key_data = key_resp.json()

        encrypted = _encrypt(key_data["key"], payload)

        put_resp = requests.put(
            f"{API}/repos/{settings.repo}/actions/secrets/{SECRET_NAME}",
            headers=_headers(settings.gh_pat),
            json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
            timeout=30,
        )
        if put_resp.status_code not in (201, 204):
            return False, f"回寫失敗 HTTP {put_resp.status_code}: {put_resp.text[:160]}"
    except SecretWriteError as exc:
        return False, str(exc)
    except requests.RequestException as exc:
        return False, f"連線 GitHub API 失敗：{exc}"
    except Exception as exc:  # noqa: BLE001
        return False, f"{exc.__class__.__name__}: {exc}"

    log(f"已把 {len(accounts)} 個帳號的最新 token 加密回寫到 {SECRET_NAME}", level="ok")
    return True, f"已回寫 {SECRET_NAME}"
