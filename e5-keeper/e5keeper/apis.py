"""Microsoft Graph API 目錄。

每一條就是保活精靈可能呼叫的一個端點。
想加自己的 API，照著 ApiSpec 的欄位往 CATALOG 裡加一筆即可。

  tolerate  = 這些狀態碼算「預期內的非成功」，通知裡標 ⚠️ 而不是 ❌
              （例如沒設大頭貼會回 404、沒有系統管理員權限會回 403）
  modes     = 這個端點在哪些認證模式下可用
  write     = 會產生資料的操作（受 config.yml 的 features.write_operations 控制）
"""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from .graph import ApiResult, GraphClient
from .utils import truncate

BOTH = ("delegated", "app")
DELEGATED = ("delegated",)


@dataclass
class ApiSpec:
    id: str
    category: str
    name: str
    path: Any = ""                       # str 或 callable(client) -> str
    method: str = "GET"
    modes: tuple = BOTH
    beta: bool = False
    write: bool = False
    self_mail: bool = False              # 受 features.send_self_mail 單獨控制
    tolerate: tuple = ()
    headers: dict | None = None
    json_body: Any = None
    summarize: Callable[[Any], str] | None = None
    func: Callable[[GraphClient, ApiResult], None] | None = None
    weight: int = 1                      # 隨機抽樣時的權重，越大越常被抽到


# ══════════════════ 摘要函式 ══════════════════

def _n(payload):
    v = payload.get("value") if isinstance(payload, dict) else None
    return len(v) if isinstance(v, list) else 0


def s_count(unit: str) -> Callable[[Any], str]:
    return lambda p: f"{_n(p)} {unit}"


def s_inbox(p):
    return f"未讀 {p.get('unreadItemCount', '?')} / 共 {p.get('totalItemCount', '?')} 封"


def s_drive(p):
    q = p.get("quota") or {}
    used, total = q.get("used"), q.get("total")
    if used is None or not total:
        return p.get("driveType", "OK")
    return f"已用 {used / 2**30:.2f} GB / {total / 2**30:.0f} GB（{used / total * 100:.1f}%）"


def s_me(p):
    return truncate(f"{p.get('displayName', '?')}", 40)


def s_org(p):
    v = (p.get("value") or [{}])[0]
    return truncate(v.get("displayName", "OK"), 40)


def s_skus(p):
    names = [x.get("skuPartNumber", "") for x in (p.get("value") or [])]
    e5 = [n for n in names if "E5" in n or "DEVELOPER" in n.upper()]
    head = e5[0] if e5 else (names[0] if names else "")
    return f"{len(names)} 個授權" + (f"｜{truncate(head, 28)}" if head else "")


def s_delta(p):
    return f"{_n(p)} 筆變更"


def s_bytes(p):
    if isinstance(p, dict) and "_raw_bytes" in p:
        return f"報表 {p['_raw_bytes']:,} bytes"
    return "OK"


# ══════════════════ 寫入型流程 ══════════════════

def _rand(n: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _cycle(
    client: GraphClient,
    result: ApiResult,
    *,
    create: tuple,             # (method, path, json_body) 或 (method, path, json_body, headers)
    delete_path: Callable[[dict], str] | None,
    label: Callable[[dict], str],
) -> None:
    """建立 →（依設定）刪除。建立成功但清除失敗會降級成 ⚠️，並附上原因。"""
    cleanup = client.cleanup_after_write
    method, path, body = create[0], create[1], create[2]
    headers = create[3] if len(create) > 3 else None

    status, payload, error, elapsed, attempts = client.request(
        method, path, json_body=body, headers=headers
    )
    result.status = status
    result.elapsed = elapsed
    result.attempts = attempts
    result.ok = 200 <= status < 300
    if not result.ok:
        result.error = truncate(error or f"HTTP {status}", 110)
        return

    obj = payload if isinstance(payload, dict) else {}
    result.summary = label(obj)

    if cleanup and delete_path is not None and obj.get("id"):
        d_status, _, d_error, d_elapsed, _ = client.request("DELETE", delete_path(obj))
        result.elapsed += d_elapsed
        if 200 <= d_status < 300:
            result.summary += " ↺已清除"
        else:
            # 東西建出來了卻沒刪掉 —— 不能報成完全成功，否則會慢慢累積垃圾
            result.ok = False
            result.tolerated = True
            result.error = truncate(
                f"已建立但清除失敗({d_status})：{result.summary}｜{d_error}", 110
            )
            result.summary += f" ⚠清除失敗({d_status})"


def w_drive_file(client: GraphClient, result: ApiResult) -> None:
    name = f"e5keeper-{_stamp()}-{_rand()}.txt"
    content = f"E5 keepalive probe {datetime.now(timezone.utc).isoformat()}\n"
    path = f"{{u}}/drive/root:/E5Keeper/{name}:/content"
    status, payload, error, elapsed, attempts = client.request(
        "PUT", path,
        data=content.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )
    result.status = status
    result.elapsed = elapsed
    result.attempts = attempts
    result.ok = 200 <= status < 300
    if not result.ok:
        result.error = truncate(error or f"HTTP {status}", 110)
        return

    obj = payload if isinstance(payload, dict) else {}
    size = obj.get("size", len(content))
    result.summary = f"已上傳 {truncate(obj.get('name', name), 28)}（{size}B）"
    if client.cleanup_after_write and obj.get("id"):
        d_status, _, d_error, d_elapsed, _ = client.request(
            "DELETE", f"{{u}}/drive/items/{obj['id']}"
        )
        result.elapsed += d_elapsed
        if 200 <= d_status < 300:
            result.summary += " ↺已清除"
        else:
            result.ok = False
            result.tolerated = True
            result.error = truncate(f"已上傳但清除失敗({d_status})：{name}｜{d_error}", 110)
            result.summary += f" ⚠清除失敗({d_status})"


def w_drive_folder(client: GraphClient, result: ApiResult) -> None:
    name = f"e5k-{_stamp()}-{_rand(4)}"
    _cycle(
        client, result,
        create=("POST", "{u}/drive/root/children", {
            "name": name,
            "folder": {},
            "@microsoft.graph.conflictBehavior": "rename",
        }),
        delete_path=lambda o: f"{{u}}/drive/items/{o['id']}",
        label=lambda o: f"已建資料夾 {truncate(o.get('name', name), 28)}",
    )


def w_mail_draft(client: GraphClient, result: ApiResult) -> None:
    _cycle(
        client, result,
        create=("POST", "{u}/messages", {
            "subject": f"[E5Keeper] 保活草稿 {_stamp()}",
            "body": {"contentType": "Text", "content": "自動產生的保活草稿，建立後隨即刪除。"},
            "toRecipients": [],
        }),
        delete_path=lambda o: f"{{u}}/messages/{o['id']}",
        label=lambda o: f"已建草稿 {truncate(o.get('subject', ''), 26)}",
    )


def w_calendar_event(client: GraphClient, result: ApiResult) -> None:
    start = datetime.now(timezone.utc) + timedelta(days=random.randint(30, 200))
    end = start + timedelta(minutes=30)
    _cycle(
        client, result,
        create=("POST", "{u}/events", {
            "subject": f"[E5Keeper] 保活事件 {_stamp()}",
            "start": {"dateTime": start.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
            "end": {"dateTime": end.strftime("%Y-%m-%dT%H:%M:%S"), "timeZone": "UTC"},
            "isReminderOn": False,
        }),
        delete_path=lambda o: f"{{u}}/events/{o['id']}",
        label=lambda o: f"已建事件 {start.strftime('%Y-%m-%d')}",
    )


def w_contact(client: GraphClient, result: ApiResult) -> None:
    _cycle(
        client, result,
        create=("POST", "{u}/contacts", {
            "givenName": "E5Keeper",
            "surname": _rand(5),
            "emailAddresses": [{"address": f"probe-{_rand()}@example.invalid", "name": "E5Keeper Probe"}],
        }),
        delete_path=lambda o: f"{{u}}/contacts/{o['id']}",
        label=lambda o: f"已建聯絡人 {truncate(o.get('displayName', ''), 24)}",
    )


def w_todo_list(client: GraphClient, result: ApiResult) -> None:
    _cycle(
        client, result,
        create=("POST", "{u}/todo/lists", {"displayName": f"E5Keeper {_stamp()}"}),
        delete_path=lambda o: f"{{u}}/todo/lists/{o['id']}",
        label=lambda o: f"已建待辦清單 {truncate(o.get('displayName', ''), 22)}",
    )


def w_send_self(client: GraphClient, result: ApiResult) -> None:
    """寄一封信給自己 —— 活動訊號最強的一種操作。"""
    target = client.self_address or ""
    if not target:
        result.skipped = True
        result.summary = "找不到收件地址"
        return
    body = {
        "message": {
            "subject": f"[E5Keeper] 保活訊號 {_stamp()}",
            "body": {
                "contentType": "Text",
                "content": "這是 E5 保活精靈自動寄出的保活郵件，可直接刪除。",
            },
            "toRecipients": [{"emailAddress": {"address": target}}],
        },
        "saveToSentItems": True,
    }
    status, _, error, elapsed, attempts = client.request("POST", "{u}/sendMail", json_body=body)
    result.status = status
    result.elapsed = elapsed
    result.attempts = attempts
    result.ok = 200 <= status < 300
    if result.ok:
        result.summary = "已寄給自己"
    else:
        result.error = truncate(error or f"HTTP {status}", 110)


# ══════════════════ 動態路徑 ══════════════════

def p_calendar_view(_client) -> str:
    now = datetime.now(timezone.utc)
    start = now.strftime("%Y-%m-%dT00:00:00Z")
    end = (now + timedelta(days=14)).strftime("%Y-%m-%dT00:00:00Z")
    return f"{{u}}/calendarView?startDateTime={start}&endDateTime={end}&$top=10"


# ══════════════════ API 目錄 ══════════════════

CATALOG: list[ApiSpec] = [
    # ── 郵件 / Outlook ────────────────────────────────────────
    ApiSpec("mail.list", "mail", "列出郵件",
            "{u}/messages?$top=10&$select=subject,receivedDateTime,isRead",
            summarize=s_count("封"), weight=3),
    ApiSpec("mail.folders", "mail", "郵件資料夾",
            "{u}/mailFolders?$top=20", summarize=s_count("個資料夾")),
    ApiSpec("mail.inbox", "mail", "收件匣狀態",
            "{u}/mailFolders/inbox?$select=unreadItemCount,totalItemCount",
            summarize=s_inbox, weight=2),
    ApiSpec("mail.inbox.recent", "mail", "收件匣最新郵件",
            "{u}/mailFolders/inbox/messages?$top=5&$select=subject,from",
            summarize=s_count("封")),
    ApiSpec("mail.attachments", "mail", "含附件郵件",
            "{u}/messages?$filter=hasAttachments eq true&$top=5&$select=subject",
            summarize=s_count("封")),
    ApiSpec("mail.delta", "mail", "收件匣差異查詢",
            "{u}/mailFolders/inbox/messages/delta",
            headers={"Prefer": "odata.maxpagesize=5"}, summarize=s_delta),
    ApiSpec("mail.categories", "mail", "郵件分類標籤",
            "{u}/outlook/masterCategories", summarize=s_count("個標籤")),
    ApiSpec("mail.rules", "mail", "收件匣規則",
            "{u}/mailFolders/inbox/messageRules",
            tolerate=(403,), summarize=s_count("條規則")),
    ApiSpec("mail.settings", "mail", "信箱設定",
            "{u}/mailboxSettings", tolerate=(403,),
            summarize=lambda p: truncate(p.get("timeZone", "OK"), 30)),
    ApiSpec("mail.draft.cycle", "mail", "建立並刪除草稿",
            write=True, func=w_mail_draft, weight=2),
    ApiSpec("mail.send.self", "mail", "寄信給自己",
            write=True, self_mail=True, func=w_send_self, weight=2),

    # ── OneDrive / SharePoint ────────────────────────────────
    ApiSpec("files.drive", "files", "雲端硬碟資訊",
            "{u}/drive", summarize=s_drive, weight=3),
    ApiSpec("files.root", "files", "根目錄檔案",
            "{u}/drive/root/children?$top=20", summarize=s_count("個項目"), weight=2),
    ApiSpec("files.recent", "files", "最近開啟的檔案",
            "{u}/drive/recent", modes=DELEGATED, summarize=s_count("個項目")),
    ApiSpec("files.shared", "files", "他人分享給我的",
            "{u}/drive/sharedWithMe", modes=DELEGATED, tolerate=(403,),
            summarize=s_count("個項目")),
    ApiSpec("files.delta", "files", "雲端硬碟差異查詢",
            "{u}/drive/root/delta", summarize=s_delta),
    ApiSpec("files.drives", "files", "所有磁碟機",
            "{u}/drives", summarize=s_count("個磁碟機")),
    ApiSpec("files.search", "files", "搜尋檔案",
            "{u}/drive/root/search(q='e5')?$top=5", summarize=s_count("個結果")),
    ApiSpec("files.documents", "files", "文件特殊資料夾",
            "{u}/drive/special/documents", tolerate=(404,),
            summarize=lambda p: truncate(p.get("name", "OK"), 30)),
    ApiSpec("files.upload.cycle", "files", "上傳並刪除測試檔",
            write=True, func=w_drive_file, weight=2),
    ApiSpec("files.folder.cycle", "files", "建立並刪除資料夾",
            write=True, func=w_drive_folder),

    # ── 行事曆 / 待辦 / 聯絡人 / OneNote ──────────────────────
    ApiSpec("cal.events", "calendar", "行事曆事件",
            "{u}/events?$top=10&$select=subject,start,end", summarize=s_count("個事件"), weight=2),
    ApiSpec("cal.calendars", "calendar", "所有行事曆",
            "{u}/calendars", summarize=s_count("個行事曆")),
    ApiSpec("cal.view", "calendar", "未來兩週行程",
            p_calendar_view, summarize=s_count("個行程")),
    ApiSpec("cal.contacts", "calendar", "聯絡人",
            "{u}/contacts?$top=10", summarize=s_count("位聯絡人")),
    ApiSpec("cal.contactfolders", "calendar", "聯絡人資料夾",
            "{u}/contactFolders", summarize=s_count("個資料夾")),
    ApiSpec("cal.todo.lists", "calendar", "待辦清單",
            "{u}/todo/lists", tolerate=(403,), summarize=s_count("個清單")),
    ApiSpec("cal.onenote.books", "calendar", "OneNote 筆記本",
            "{u}/onenote/notebooks", tolerate=(403, 404), summarize=s_count("本筆記")),
    ApiSpec("cal.onenote.sections", "calendar", "OneNote 章節",
            "{u}/onenote/sections?$top=10", tolerate=(403, 404), summarize=s_count("個章節")),
    ApiSpec("cal.event.cycle", "calendar", "建立並刪除行事曆事件",
            write=True, func=w_calendar_event),
    ApiSpec("cal.contact.cycle", "calendar", "建立並刪除聯絡人",
            write=True, func=w_contact),
    ApiSpec("cal.todo.cycle", "calendar", "建立並刪除待辦清單",
            write=True, func=w_todo_list, tolerate=(403,)),

    # ── 使用者 / 目錄 / Teams / 報表 ──────────────────────────
    ApiSpec("dir.me", "directory", "個人資料",
            "{u}?$select=displayName,userPrincipalName,mail,id",
            summarize=s_me, weight=3),
    ApiSpec("dir.users", "directory", "組織使用者",
            "/users?$top=10&$select=displayName,userPrincipalName",
            tolerate=(403,), summarize=s_count("位使用者"), weight=2),
    ApiSpec("dir.groups", "directory", "群組",
            "/groups?$top=10&$select=displayName", tolerate=(403,), summarize=s_count("個群組")),
    ApiSpec("dir.teams", "directory", "已加入的 Teams",
            "{u}/joinedTeams", tolerate=(403,), summarize=s_count("個團隊")),
    ApiSpec("dir.people", "directory", "常聯絡的人",
            "{u}/people?$top=10", modes=DELEGATED, tolerate=(403,), summarize=s_count("位")),
    ApiSpec("dir.memberof", "directory", "所屬群組",
            "{u}/memberOf?$top=10", tolerate=(403,), summarize=s_count("個")),
    ApiSpec("dir.org", "directory", "組織資訊",
            "/organization", tolerate=(403,), summarize=s_org),
    ApiSpec("dir.domains", "directory", "網域清單",
            "/domains", tolerate=(403,), summarize=s_count("個網域")),
    ApiSpec("dir.roles", "directory", "目錄角色",
            "/directoryRoles", tolerate=(403,), summarize=s_count("個角色")),
    ApiSpec("dir.skus", "directory", "訂閱授權",
            "/subscribedSkus", tolerate=(403,), summarize=s_skus, weight=2),
    ApiSpec("dir.licenses", "directory", "個人授權明細",
            "{u}/licenseDetails", tolerate=(403,), summarize=s_count("項授權")),
    ApiSpec("dir.sites", "directory", "SharePoint 根網站",
            "/sites/root", tolerate=(403,), summarize=lambda p: truncate(p.get("displayName", "OK"), 30)),
    ApiSpec("dir.sitelists", "directory", "SharePoint 清單",
            "/sites/root/lists?$top=10", tolerate=(403,), summarize=s_count("個清單")),
    ApiSpec("dir.apps", "directory", "已註冊應用程式",
            "/applications?$top=5&$select=displayName", tolerate=(403,), summarize=s_count("個應用")),
    ApiSpec("dir.sp", "directory", "服務主體",
            "/servicePrincipals?$top=5&$select=displayName", tolerate=(403,), summarize=s_count("個")),
    ApiSpec("dir.photo", "directory", "大頭貼中繼資料",
            "{u}/photo", tolerate=(404, 403),
            summarize=lambda p: f"{p.get('width', '?')}×{p.get('height', '?')}"),
    ApiSpec("dir.manager", "directory", "主管",
            "{u}/manager", tolerate=(404, 403), summarize=s_me),
    ApiSpec("dir.authmethods", "directory", "驗證方式",
            "{u}/authentication/methods", tolerate=(403,), summarize=s_count("種")),
    ApiSpec("dir.report.active", "directory", "Office 365 活躍使用者報表",
            "/reports/getOffice365ActiveUserDetail(period='D7')",
            tolerate=(403,), summarize=s_bytes),
    ApiSpec("dir.report.teams", "directory", "Teams 使用量報表",
            "/reports/getTeamsUserActivityUserDetail(period='D7')",
            tolerate=(403,), summarize=s_bytes),
]


# ══════════════════ 篩選與抽樣 ══════════════════

def available(
    account,
    categories: list[str],
    *,
    allow_write: bool,
    allow_self_mail: bool,
) -> list[ApiSpec]:
    """列出這個帳號在目前設定下可以跑的 API。"""
    cats = set(categories or [])
    out = []
    for spec in CATALOG:
        if cats and spec.category not in cats:
            continue
        if account.mode not in spec.modes:
            continue
        if spec.write and not allow_write:
            continue
        if spec.self_mail and not allow_self_mail:
            continue
        out.append(spec)
    return out


def sample(specs: list[ApiSpec], low: int, high: int) -> list[ApiSpec]:
    """依權重隨機抽 low~high 個，再打亂順序。"""
    if not specs:
        return []
    low = max(1, min(low, len(specs)))
    high = max(low, min(high, len(specs)))
    k = random.randint(low, high)

    pool = list(specs)
    picked: list[ApiSpec] = []
    while pool and len(picked) < k:
        weights = [max(1, s.weight) for s in pool]
        chosen = random.choices(pool, weights=weights, k=1)[0]
        pool.remove(chosen)
        picked.append(chosen)
    random.shuffle(picked)
    return picked
