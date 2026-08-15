"""Microsoft Graph 用戶端：統一的呼叫、重試、結果整理。"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import requests

from .utils import fmt_duration, log, truncate

GRAPH_V1 = "https://graph.microsoft.com/v1.0"
GRAPH_BETA = "https://graph.microsoft.com/beta"

# 這些狀態碼代表「等一下再試就會好」
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


@dataclass
class ApiResult:
    """一次 API 呼叫的結果。"""

    spec_id: str
    name: str
    category: str
    status: int = 0
    ok: bool = False           # 2xx
    tolerated: bool = False    # 非 2xx，但屬於預期內（例如沒有大頭貼 → 404）
    elapsed: float = 0.0
    summary: str = ""
    error: str = ""
    attempts: int = 1
    skipped: bool = False

    @property
    def icon(self) -> str:
        if self.skipped:
            return "⏭️"
        if self.ok:
            return "✅"
        if self.tolerated:
            return "⚠️"
        return "❌"

    @property
    def counts_as_success(self) -> bool:
        return self.ok or self.tolerated or self.skipped


@dataclass
class GraphClient:
    access_token: str
    user_ref: str = "/me"
    self_address: str = ""            # 寄信給自己時的收件地址
    cleanup_after_write: bool = True  # 寫入型操作完成後是否立刻刪除
    timeout: int = 30
    max_attempts: int = 3
    initial_backoff: float = 2.0
    multiplier: float = 2.0
    session: requests.Session = field(default_factory=requests.Session)

    # ── 低階請求 ──────────────────────────────────────────────
    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "User-Agent": "E5Keeper/1.0",
        }
        if extra:
            headers.update(extra)
        return headers

    def resolve(self, path: str, beta: bool = False) -> str:
        path = path.replace("{u}", self.user_ref)
        base = GRAPH_BETA if beta else GRAPH_V1
        if not path.startswith("/"):
            path = "/" + path
        return base + path

    def request(
        self,
        method: str,
        path: str,
        *,
        beta: bool = False,
        headers: dict | None = None,
        json_body: Any = None,
        data: bytes | None = None,
        retry: bool = True,
    ) -> tuple[int, Any, str, float, int]:
        """回傳 (status, payload, error, elapsed, attempts)。"""
        url = self.resolve(path, beta)
        attempts = 0
        backoff = self.initial_backoff
        started = time.monotonic()
        last_error = ""
        status = 0
        payload: Any = None
        limit = self.max_attempts if retry else 1

        while attempts < limit:
            attempts += 1
            try:
                resp = self.session.request(
                    method.upper(),
                    url,
                    headers=self._headers(headers),
                    json=json_body if data is None else None,
                    data=data,
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last_error = f"連線錯誤：{exc.__class__.__name__}"
                status = 0
                if attempts < limit:
                    time.sleep(backoff)
                    backoff *= self.multiplier
                    continue
                break

            status = resp.status_code
            payload, last_error = _decode(resp)

            if status in RETRYABLE_STATUS and attempts < limit:
                wait = backoff
                retry_after = resp.headers.get("Retry-After")
                if retry_after:
                    try:
                        wait = min(float(retry_after), 60.0)
                    except ValueError:
                        pass
                log(f"{method} {path} → {status}，{wait:.0f}s 後重試", level="warn")
                time.sleep(wait)
                backoff *= self.multiplier
                continue
            break

        return status, payload, last_error, time.monotonic() - started, attempts

    # ── 高階：跑一個 ApiSpec ──────────────────────────────────
    def call(self, spec) -> ApiResult:
        result = ApiResult(spec_id=spec.id, name=spec.name, category=spec.category)

        if spec.func is not None:
            try:
                spec.func(self, result)
            except Exception as exc:  # noqa: BLE001 — 單一 API 不該弄垮整輪
                result.ok = False
                result.error = f"{exc.__class__.__name__}: {exc}"
            # 自訂流程也要吃到 tolerate 設定（例如沒有 Tasks 權限時的 403）
            if not result.ok and not result.skipped and result.status in spec.tolerate:
                result.tolerated = True
            return result

        path = spec.path(self) if callable(spec.path) else spec.path
        status, payload, error, elapsed, attempts = self.request(
            spec.method, path, beta=spec.beta, headers=spec.headers, json_body=spec.json_body
        )
        result.status = status
        result.elapsed = elapsed
        result.attempts = attempts
        result.ok = 200 <= status < 300
        result.tolerated = (not result.ok) and status in spec.tolerate

        if result.ok:
            result.summary = _summarize(spec, payload)
        else:
            result.error = truncate(error or f"HTTP {status}", 110)
        return result


def _decode(resp: requests.Response) -> tuple[Any, str]:
    """把回應轉成 (payload, error_message)。"""
    ctype = resp.headers.get("Content-Type", "")
    if "json" in ctype:
        try:
            data = resp.json()
        except ValueError:
            return None, "回應不是合法 JSON"
        if resp.ok:
            return data, ""
        err = data.get("error") if isinstance(data, dict) else None
        if isinstance(err, dict):
            code = err.get("code", "")
            msg = err.get("message", "")
            return data, f"{code}: {msg}" if code else str(msg)
        return data, truncate(str(data), 160)

    # 報表類 API 會回 CSV
    if resp.ok:
        return {"_raw_bytes": len(resp.content), "_ctype": ctype}, ""
    return None, truncate(resp.text or f"HTTP {resp.status_code}", 160)


def _summarize(spec, payload: Any) -> str:
    if spec.summarize is not None:
        try:
            text = spec.summarize(payload)
            if text:
                return truncate(text, 60)
        except Exception:  # noqa: BLE001
            pass
    return default_summary(payload)


def default_summary(payload: Any) -> str:
    if payload is None:
        return "無內容"
    if isinstance(payload, dict):
        if "_raw_bytes" in payload:
            return f"{payload['_raw_bytes']:,} bytes"
        if isinstance(payload.get("value"), list):
            n = len(payload["value"])
            more = "+" if payload.get("@odata.nextLink") else ""
            return f"{n}{more} 筆"
        for key in ("displayName", "subject", "name", "id"):
            if payload.get(key):
                return truncate(str(payload[key]), 50)
        return "OK"
    if isinstance(payload, list):
        return f"{len(payload)} 筆"
    return truncate(str(payload), 50)


def fmt_result_line(r: ApiResult, id_width: int = 26) -> str:
    """排成等寬一行，給 Telegram 的 <pre> 區塊用。"""
    if r.skipped:
        tail = r.summary or "略過"
        return f"{r.icon} --- {r.spec_id.ljust(id_width)} {tail}"
    code = str(r.status) if r.status else "ERR"
    tail = r.summary if (r.ok and r.summary) else (r.error or "")
    retry_mark = f" ↻{r.attempts - 1}" if r.attempts > 1 else ""
    return (
        f"{r.icon} {code.rjust(3)} {r.spec_id.ljust(id_width)} "
        f"{fmt_duration(r.elapsed).rjust(6)}{retry_mark}  {tail}"
    ).rstrip()
