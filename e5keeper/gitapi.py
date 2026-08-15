"""用 GitHub GraphQL API 建立**帶簽章**的 commit。

一般 `git push` 推上去的 commit 在 GitHub 上不會有 Verified 標記，
除非你自己管一把 GPG／SSH 金鑰、還要把私鑰塞進 Secrets。

改走 `createCommitOnBranch` 這個 mutation 就完全不用碰金鑰 ——
GitHub 官方文件寫得很明白：

  > Commits made using this mutation are automatically signed by GitHub
  > if supported and will be marked as verified in the user interface.

也就是說 commit 一定顯示 ✅ Verified，簽章由 GitHub 自己蓋。

用哪個 token 決定 commit 顯示成誰：
  · GH_PAT       → 顯示為你本人，會計進你的貢獻圖
  · GITHUB_TOKEN → 顯示為 github-actions[bot]
"""

from __future__ import annotations

import base64
import os
from pathlib import Path

import requests

from .utils import log, truncate

REST = "https://api.github.com"
GRAPHQL = "https://api.github.com/graphql"

MUTATION = """
mutation ($input: CreateCommitOnBranchInput!) {
  createCommitOnBranch(input: $input) {
    commit { oid url }
  }
}
"""

# 這些字眼代表「你手上的 HEAD 已經過期」，重新同步後再試就會成功
_STALE_HINTS = ("expected", "stale", "not a fast forward", "is at", "changed")


class CommitError(Exception):
    def __init__(self, message: str, *, retryable: bool = False, fatal: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.fatal = fatal      # fatal = 換個方式也沒用，直接退回 git push


def candidate_tokens() -> list[tuple[str, str]]:
    """依優先順序列出可用的 token，回傳 [(token, 說明), ...]。

    先試 GH_PAT —— commit 會顯示成你本人並計進貢獻圖。
    PAT 沒有 Contents 權限時，再試 Actions 內建的 GITHUB_TOKEN ——
    作者變成 github-actions[bot]，但**一樣有 Verified 標記**，
    這比直接退回沒有簽章的 git push 好。
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, label in (
        ("GH_PAT", "GH_PAT（顯示為你本人）"),
        ("GITHUB_TOKEN", "GITHUB_TOKEN（顯示為 github-actions[bot]）"),
    ):
        value = os.environ.get(name, "").strip()
        if value and value not in seen:
            seen.add(value)
            out.append((value, label))
    return out


def pick_token() -> tuple[str, str]:
    """只要最優先的那一個，給日誌與 validate 用。"""
    tokens = candidate_tokens()
    return tokens[0] if tokens else ("", "")


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "E5Keeper/1.0",
    }


def whoami(token: str, timeout: int = 20) -> str:
    """查這個 token 對應的身分，純粹是為了在日誌裡講清楚。"""
    try:
        resp = requests.get(f"{REST}/user", headers=_headers(token), timeout=timeout)
        if resp.ok:
            return resp.json().get("login", "") or ""
    except requests.RequestException:
        pass
    return ""


def last_workflow_run(
    token: str, repo: str, workflow_file: str, timeout: int = 20
) -> tuple[str, str]:
    """查某個 workflow 最後一次執行的 (結論, 完成時間)。

    用來確認「指令通道還活著嗎」。輪詢 workflow 是靠 GitHub 排程觸發的，
    而排程可能被停用、可能因為額度或設定錯誤而每次失敗 ——
    這些情況下你不會收到任何通知，只會覺得「指令怎麼沒反應」。
    保活執行時順手查一下，掛了就在通知裡講。
    """
    try:
        resp = requests.get(
            f"{REST}/repos/{repo}/actions/workflows/{workflow_file}/runs",
            headers=_headers(token),
            params={"per_page": 1, "exclude_pull_requests": "true"},
            timeout=timeout,
        )
        if not resp.ok:
            return "", ""
        runs = (resp.json() or {}).get("workflow_runs") or []
        if not runs:
            return "none", ""
        run = runs[0]
        return (run.get("conclusion") or run.get("status") or "",
                run.get("updated_at") or run.get("created_at") or "")
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return "", ""


def get_head_oid(token: str, repo: str, branch: str, timeout: int = 30) -> str:
    """取得遠端分支目前的 HEAD commit SHA。"""
    try:
        resp = requests.get(
            f"{REST}/repos/{repo}/git/ref/heads/{branch}",
            headers=_headers(token), timeout=timeout,
        )
    except requests.RequestException as exc:
        raise CommitError(f"連線 GitHub API 失敗：{exc}", retryable=True) from exc

    if resp.status_code == 404:
        raise CommitError(f"找不到分支 {branch}（或 token 沒有這個 repo 的權限）", fatal=True)
    if resp.status_code in (401, 403):
        raise CommitError(
            f"token 權限不足（HTTP {resp.status_code}）：需要 Contents 的讀寫權限", fatal=True
        )
    if not resp.ok:
        raise CommitError(f"查詢分支失敗 HTTP {resp.status_code}", retryable=True)

    try:
        return resp.json()["object"]["sha"]
    except (KeyError, TypeError, ValueError) as exc:
        raise CommitError("分支查詢結果格式不符預期", retryable=True) from exc


def create_signed_commit(
    token: str,
    repo: str,
    branch: str,
    message: str,
    expected_oid: str,
    additions: list[tuple[str, bytes]],
    deletions: list[str] | None = None,
    timeout: int = 60,
) -> tuple[str, str]:
    """建立一個由 GitHub 簽章的 commit，回傳 (新的 oid, commit 網址)。

    additions 是 [(repo 內的相對路徑, 檔案位元組), ...]
    """
    headline, _, body = message.partition("\n")
    payload = {
        "query": MUTATION,
        "variables": {
            "input": {
                "branch": {"repositoryNameWithOwner": repo, "branchName": branch},
                "message": {"headline": headline[:200], "body": body.strip()},
                "expectedHeadOid": expected_oid,
                "fileChanges": {
                    "additions": [
                        {"path": path, "contents": base64.b64encode(data).decode("ascii")}
                        for path, data in additions
                    ],
                    "deletions": [{"path": p} for p in (deletions or [])],
                },
            }
        },
    }

    try:
        resp = requests.post(GRAPHQL, headers=_headers(token), json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise CommitError(f"連線 GraphQL API 失敗：{exc}", retryable=True) from exc

    if resp.status_code in (401, 403):
        raise CommitError(
            f"token 權限不足（HTTP {resp.status_code}）："
            "細粒度 PAT 需要 Contents = Read and write", fatal=True
        )
    if not resp.ok:
        raise CommitError(
            f"GraphQL 回應 HTTP {resp.status_code}: {truncate(resp.text, 200)}", retryable=True
        )

    try:
        data = resp.json()
    except ValueError as exc:
        raise CommitError("GraphQL 回應不是 JSON", retryable=True) from exc

    errors = data.get("errors") or []
    if errors:
        text = "；".join(str(e.get("message", e)) for e in errors)
        low = text.lower()
        if any(hint in low for hint in _STALE_HINTS):
            raise CommitError(f"分支已被其他執行更新：{truncate(text, 160)}", retryable=True)
        if "not have permission" in low or "resource not accessible" in low:
            raise CommitError(f"權限不足：{truncate(text, 160)}", fatal=True)
        raise CommitError(truncate(text, 200))

    commit = (((data.get("data") or {}).get("createCommitOnBranch") or {}).get("commit") or {})
    oid = commit.get("oid", "")
    if not oid:
        raise CommitError("GraphQL 沒有回傳 commit oid", retryable=True)
    return oid, commit.get("url", "")


def commit_files(
    token: str,
    repo: str,
    branch: str,
    message: str,
    expected_oid: str,
    root: Path,
    changes: list[tuple[str, str]],
) -> tuple[str, str]:
    """把 `git status` 判定出來的變更包成一個簽章 commit 送出。

    changes 是 [(相對路徑, 'A' 新增或修改 / 'D' 刪除), ...]
    """
    additions: list[tuple[str, bytes]] = []
    deletions: list[str] = []
    for rel, kind in changes:
        if kind == "D":
            deletions.append(rel)
            continue
        full = root / rel
        if not full.is_file():
            continue
        additions.append((rel, full.read_bytes()))

    if not additions and not deletions:
        raise CommitError("沒有任何檔案變更")

    total = sum(len(d) for _, d in additions)
    log(f"準備簽章提交：{len(additions)} 個檔案新增/修改、{len(deletions)} 個刪除"
        f"（共 {total:,} bytes）")
    return create_signed_commit(
        token, repo, branch, message, expected_oid, additions, deletions
    )
