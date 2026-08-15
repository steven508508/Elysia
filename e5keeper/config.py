"""設定載入：config.yml（行為參數）+ 環境變數 / Secrets（機密）。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .utils import gh_add_mask, log, mask_email

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yml"

DEFAULTS: dict[str, Any] = {
    "timezone": "Asia/Taipei",
    "schedule": {"random_delay_max_minutes": 50},
    "run": {
        "min_apis": 8,
        "max_apis": 15,
        "api_delay_seconds": [1, 8],
        "account_delay_seconds": [5, 45],
        "timeout_seconds": 30,
        "retry": {"max_attempts": 3, "initial_backoff": 2, "multiplier": 2},
    },
    "features": {
        "write_operations": True,
        "send_self_mail": True,
        "cleanup_after_write": True,
        "subscription_reminder": True,
        "reminder_days": [30, 14, 7, 3, 1],
        "history": True,
        "history_detail": "minimal",   # minimal | full
        "weekly_report": True,
    },
    "notify": {
        "mask_email": True,
        "detail": "full",
        "max_detail_lines": 60,
        "silent_success": False,
    },
    "api_categories": ["mail", "files", "calendar", "directory"],
}

# 委派模式預設要求的權限範圍（get_token.py 也用同一份）
DEFAULT_SCOPES = [
    "offline_access",
    "openid",
    "profile",
    "User.Read",
    "User.ReadBasic.All",
    "Mail.ReadWrite",
    "Mail.Send",
    "MailboxSettings.Read",
    "Files.ReadWrite.All",
    "Calendars.ReadWrite",
    "Contacts.ReadWrite",
    "Tasks.ReadWrite",
    "Notes.Read",
    "People.Read",
    "Sites.Read.All",
    "Team.ReadBasic.All",
    "Directory.Read.All",
    # 下面兩個是選用的。Azure 沒加對應權限的話，
    # dir.authmethods 與 dir.report.* 會顯示 ⚠️ 403，不影響保活。
    # Reports.Read.All 除了權限之外，帳號還要有 Entra 管理員角色才讀得到。
    "UserAuthenticationMethod.Read",
    "Reports.Read.All",
]

# Azure PowerShell 的公用 client id，沒有自建應用程式時可先拿來測試
FALLBACK_CLIENT_ID = "1950a258-227b-4e31-a9cf-717495945fc2"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        elif value is not None:
            out[key] = value
    return out


@dataclass
class Account:
    """一個 E5 帳號。"""

    alias: str
    email: str
    mode: str = "delegated"          # delegated | app
    tenant: str = "common"
    client_id: str = FALLBACK_CLIENT_ID
    client_secret: str = ""
    refresh_token: str = ""
    target_user: str = ""            # app 模式要操作的使用者（預設同 email）
    scopes: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPES))
    enabled: bool = True
    index: int = 0

    @property
    def user_ref(self) -> str:
        """在 Graph 路徑中代表這個使用者的片段。"""
        if self.mode == "app":
            return f"/users/{self.target_user or self.email}"
        return "/me"

    @property
    def mode_label(self) -> str:
        return "委派權限 refresh_token" if self.mode == "delegated" else "應用程式權限 client_credentials"

    def to_secret_dict(self) -> dict:
        out = {
            "alias": self.alias,
            "email": self.email,
            "mode": self.mode,
            "tenant": self.tenant,
            "client_id": self.client_id,
        }
        if self.client_secret:
            out["client_secret"] = self.client_secret
        if self.mode == "delegated":
            out["refresh_token"] = self.refresh_token
        if self.target_user and self.target_user != self.email:
            out["target_user"] = self.target_user
        if self.scopes and self.scopes != DEFAULT_SCOPES:
            out["scopes"] = self.scopes
        if not self.enabled:
            out["enabled"] = False
        return out


@dataclass
class Settings:
    raw: dict
    accounts: list[Account]
    telegram_token: str = ""
    telegram_chat_id: str = ""
    gh_pat: str = ""
    repo: str = ""

    # ── 常用參數的捷徑 ──
    @property
    def tz(self) -> str:
        return self.raw.get("timezone", "Asia/Taipei")

    @property
    def run(self) -> dict:
        return self.raw["run"]

    @property
    def features(self) -> dict:
        return self.raw["features"]

    @property
    def notify(self) -> dict:
        return self.raw["notify"]

    @property
    def categories(self) -> list[str]:
        return list(self.raw.get("api_categories") or [])

    @property
    def can_write_secrets(self) -> bool:
        return bool(self.gh_pat and self.repo)

    def find_account(self, key: str) -> Account | None:
        key = (key or "").strip().lower()
        for acc in self.accounts:
            if key in (acc.alias.lower(), acc.email.lower()):
                return acc
        # 也接受 1-based 序號
        if key.isdigit():
            idx = int(key) - 1
            if 0 <= idx < len(self.accounts):
                return self.accounts[idx]
        return None


def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_PATH
    data: dict = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    else:
        log(f"找不到 {path.name}，改用內建預設值", level="warn")
    merged = _deep_merge(DEFAULTS, data)
    # api_categories 是清單，不能被深層合併蓋掉
    if data.get("api_categories") is not None:
        merged["api_categories"] = data["api_categories"]
    return merged


def parse_accounts(raw_json: str) -> list[Account]:
    if not raw_json or not raw_json.strip():
        raise ValueError(
            "E5_ACCOUNTS 是空的。請到 repo 的 Settings → Secrets and variables → "
            "Actions 建立名為 E5_ACCOUNTS 的 secret，內容是帳號 JSON 陣列。"
        )
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"E5_ACCOUNTS 不是合法的 JSON：{exc}") from exc

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("E5_ACCOUNTS 必須是 JSON 陣列，例如 [ {...}, {...} ]")
    if not data:
        # 明確的空陣列是合法狀態（例如帳號被 /remove 光了），
        # 跟「Secret 根本沒設」不一樣，後者在上面就已經擋掉了。
        log("E5_ACCOUNTS 是空陣列，目前沒有任何帳號", level="warn")
        return []

    accounts: list[Account] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(f"E5_ACCOUNTS 第 {i + 1} 筆不是物件")
        email = (item.get("email") or item.get("username") or "").strip()
        # 先遮蔽再做任何檢查 —— 底下的 raise 會被記進日誌，
        # 公開 repo 的日誌是任何人都看得到的，不能讓完整 email 從錯誤訊息漏出去。
        gh_add_mask(email)
        shown = mask_email(email) if email else f"第 {i + 1} 筆"
        mode = (item.get("mode") or "delegated").strip().lower()
        if mode not in ("delegated", "app"):
            raise ValueError(f"帳號 {shown} 的 mode 只能是 delegated 或 app")
        if not email:
            raise ValueError(f"E5_ACCOUNTS 第 {i + 1} 筆缺少 email")

        acc = Account(
            alias=(item.get("alias") or f"帳號{i + 1}").strip(),
            email=email,
            mode=mode,
            tenant=(item.get("tenant") or "common").strip(),
            client_id=(item.get("client_id") or FALLBACK_CLIENT_ID).strip(),
            client_secret=(item.get("client_secret") or "").strip(),
            refresh_token=(item.get("refresh_token") or "").strip(),
            target_user=(item.get("target_user") or "").strip(),
            scopes=list(item.get("scopes") or DEFAULT_SCOPES),
            enabled=item.get("enabled", True),
            index=i,
        )

        if acc.mode == "delegated" and not acc.refresh_token:
            raise ValueError(f"帳號「{acc.alias}」是 delegated 模式，但沒有 refresh_token")
        if acc.mode == "app":
            if not acc.client_secret:
                raise ValueError(f"帳號「{acc.alias}」是 app 模式，但沒有 client_secret")
            if acc.tenant in ("common", "organizations", "consumers"):
                raise ValueError(
                    f"帳號「{acc.alias}」是 app 模式，tenant 必須填實際的租用戶 ID 或網域，不能是 common"
                )

        gh_add_mask(acc.refresh_token, acc.client_secret, acc.email)
        accounts.append(acc)

    return accounts


def load_settings(
    config_path: Path | None = None, *, allow_empty_accounts: bool = False
) -> Settings:
    cfg = load_config(config_path)
    raw_accounts = os.environ.get("E5_ACCOUNTS", "")
    if allow_empty_accounts and not raw_accounts.strip():
        # 第一次授權時 E5_ACCOUNTS 還不存在，這是正常的
        accounts: list[Account] = []
    else:
        accounts = parse_accounts(raw_accounts)

    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    pat = os.environ.get("GH_PAT", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    gh_add_mask(tg_token, pat)

    return Settings(
        raw=cfg,
        accounts=accounts,
        telegram_token=tg_token,
        telegram_chat_id=tg_chat,
        gh_pat=pat,
        repo=repo,
    )
