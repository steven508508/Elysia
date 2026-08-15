#!/usr/bin/env python3
"""提交機制的端對端測試（離線、不碰真實 GitHub）。

在本機建一個真的 git repo + 假的 GitHub API，驗證四條路徑：

  ① 正常情況     → 走 GraphQL 建立由 GitHub 簽章的 Verified commit
  ② 併發衝突     → 別的 job 搶先推了，本地要重新同步再套用，**且不能蓋掉對方的紀錄**
  ③ PAT 權限不足 → 自動改用 GITHUB_TOKEN，Verified 標記保住（作者變成 bot）
  ④ 全都不行     → 退回一般 git push，紀錄照樣留得住（只是沒有 Verified）

用法：
    python tools/selftest_commit.py
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BARE: Path
SERVER_CLONE: Path
MODE = {"fail_403": False, "fail_pat_403": False, "inject_conflict": False}
COMMITS: list[dict] = []


def git(*args: str, cwd: Path, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, check=check,
                       capture_output=True, text=True, timeout=60)
    return (r.stdout or "").strip()


def head_sha() -> str:
    return git("rev-parse", "main", cwd=BARE)


# ══════════════════ 假的 GitHub API ══════════════════

class GitHubHandler(BaseHTTPRequestHandler):
    def log_message(self, *_a):
        pass

    def _send(self, code: int, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if "/git/ref/heads/" in self.path:
            return self._send(200, {"object": {"sha": head_sha(), "type": "commit"}})
        if self.path.endswith("/user"):
            return self._send(200, {"login": "csh"})
        if "/actions/secrets/public-key" in self.path:
            return self._send(200, {"key_id": "1", "key": "fake"})
        return self._send(404, {"message": "Not Found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()

        if MODE["fail_403"]:
            return self._send(403, {"message": "Resource not accessible by personal access token"})
        # 只讓 PAT 被拒，模擬「PAT 少了 Contents 權限，但 GITHUB_TOKEN 有」
        if MODE["fail_pat_403"] and token.startswith("ghp_"):
            return self._send(403, {"message": "Resource not accessible by personal access token"})

        variables = payload.get("variables", {}).get("input", {})
        expected = variables.get("expectedHeadOid", "")

        # 模擬「另一個 job 搶先推了一筆紀錄」
        if MODE["inject_conflict"]:
            MODE["inject_conflict"] = False
            _server_commit(
                [("history/2026-08.jsonl",
                  b'{"ts":"2026-08-15T00:00:00Z","from":"another-job"}\n')],
                "另一個 job 的紀錄", append=True,
            )
            return self._send(200, {"errors": [{
                "message": f"Expected branch to point to {expected} but it is at {head_sha()}",
            }]})

        if expected != head_sha():
            return self._send(200, {"errors": [{
                "message": f"Expected branch to point to {expected} but it is at {head_sha()}",
            }]})

        changes = variables.get("fileChanges", {})
        additions = [(a["path"], base64.b64decode(a["contents"]))
                     for a in changes.get("additions", [])]
        deletions = [d["path"] for d in changes.get("deletions", [])]
        message = variables.get("message", {}).get("headline", "")
        oid = _server_commit(additions, message, deletions=deletions)
        COMMITS.append({"oid": oid, "message": message, "token": token,
                        "files": sorted(p for p, _ in additions)})
        return self._send(200, {"data": {"createCommitOnBranch": {
            "commit": {"oid": oid, "url": f"https://github.com/u/r/commit/{oid}"},
        }}})


def _server_commit(additions, message: str, deletions=None, append: bool = False) -> str:
    """在假的伺服器端把變更寫進 bare repo，模擬 GitHub 真的建立 commit。"""
    git("fetch", "origin", "main", cwd=SERVER_CLONE)
    git("reset", "--hard", "origin/main", cwd=SERVER_CLONE)
    for rel, data in additions:
        target = SERVER_CLONE / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if append and target.exists():
            target.write_bytes(target.read_bytes() + data)
        else:
            target.write_bytes(data)
    for rel in deletions or []:
        (SERVER_CLONE / rel).unlink(missing_ok=True)
    git("add", "-A", cwd=SERVER_CLONE)
    git("commit", "-m", message or "commit", cwd=SERVER_CLONE)
    git("push", "origin", "HEAD:main", cwd=SERVER_CLONE)
    return head_sha()


# ══════════════════ 測試 ══════════════════

def main() -> int:
    global BARE, SERVER_CLONE

    tmp = Path(tempfile.mkdtemp(prefix="e5keeper-commit-"))
    BARE = tmp / "remote.git"
    SERVER_CLONE = tmp / "server"
    work = tmp / "work"

    subprocess.run(["git", "init", "--bare", "-b", "main", str(BARE)],
                   check=True, capture_output=True)
    work.mkdir()
    git("init", "-b", "main", cwd=work)
    git("config", "user.email", "t@t.t", cwd=work)
    git("config", "user.name", "T", cwd=work)
    (work / "README.md").write_text("init\n", encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-m", "init", cwd=work)
    git("remote", "add", "origin", str(BARE), cwd=work)
    git("push", "-u", "origin", "main", cwd=work)
    subprocess.run(["git", "clone", str(BARE), str(SERVER_CLONE)],
                   check=True, capture_output=True)
    git("config", "user.email", "gh@gh.gh", cwd=SERVER_CLONE)
    git("config", "user.name", "GitHub", cwd=SERVER_CLONE)

    server = ThreadingHTTPServer(("127.0.0.1", 0), GitHubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    print(f"假的 GitHub API 啟動於 {base}")
    print(f"測試用 repo：{work}\n")

    from e5keeper import gitapi, history

    gitapi.REST = base
    gitapi.GRAPHQL = base + "/graphql"
    history.ROOT = work
    history.HISTORY_DIR = work / "history"
    history.STATUS_FILE = work / "STATUS.md"

    os.environ.update({
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": "csh/e5-keeper",
        "GITHUB_REF_NAME": "main",
        "GH_PAT": "ghp_fake_token_for_testing",
    })
    os.environ.pop("GITHUB_TOKEN", None)

    bad = 0

    def case(name: str, ok: bool, detail: str = "") -> None:
        nonlocal bad
        print(f"  {'✅' if ok else '❌'} {name}" + (f"　→ {detail}" if not ok and detail else ""))
        if not ok:
            bad += 1

    def write_entry(line: str):
        def apply():
            history.HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            f = history.HISTORY_DIR / "2026-08.jsonl"
            existing = f.read_text(encoding="utf-8") if f.exists() else ""
            if line not in existing:
                f.write_text(existing + line + "\n", encoding="utf-8")
            history.STATUS_FILE.write_text(f"# 狀態\n\n最後一筆：{line}\n", encoding="utf-8")
        return apply

    # ── ① 正常路徑 ──
    print("① 正常情況：建立 Verified commit")
    apply = write_entry('{"ts":"2026-08-15T01:00:00Z","run":1}')
    apply()
    ok = history.commit_and_push("chore: 第一筆紀錄", ["history", "STATUS.md"], rewrite=apply)
    case("提交成功", ok)
    case("走的是 GraphQL 簽章路徑（GitHub 會標記 Verified）", len(COMMITS) == 1,
         f"COMMITS={len(COMMITS)}")
    if COMMITS:
        case("commit 內含正確的檔案",
             COMMITS[-1]["files"] == ["STATUS.md", "history/2026-08.jsonl"],
             str(COMMITS[-1]["files"]))
        case("commit 訊息帶 [skip ci]", "[skip ci]" in COMMITS[-1]["message"],
             COMMITS[-1]["message"])
    case("本地已同步到遠端（不會重複提交）",
         git("rev-parse", "HEAD", cwd=work) == head_sha())
    case("再次呼叫時正確判定「沒有變更」",
         history.commit_and_push("chore: 重複", ["history", "STATUS.md"]) is False)

    # ── ② 併發衝突 ──
    print("\n② 併發衝突：另一個 job 搶先推了一筆")
    MODE["inject_conflict"] = True
    apply2 = write_entry('{"ts":"2026-08-15T02:00:00Z","run":2}')
    apply2()
    ok2 = history.commit_and_push("chore: 第二筆紀錄", ["history", "STATUS.md"], rewrite=apply2)
    case("重新同步後提交成功", ok2)
    content = (work / "history/2026-08.jsonl").read_text(encoding="utf-8")
    case("我們自己的紀錄有寫進去", '"run":2' in content)
    case("對方的紀錄沒有被蓋掉（沒有資料遺失）", "another-job" in content,
         repr(content))
    case("第一筆紀錄還在", '"run":1' in content)

    # ── ③ PAT 權限不足 → 改用 GITHUB_TOKEN，仍保住 Verified ──
    print("\n③ PAT 少了 Contents 權限，但有 GITHUB_TOKEN 可用")
    MODE["fail_pat_403"] = True
    os.environ["GITHUB_TOKEN"] = "ghs_fake_actions_token"
    before = len(COMMITS)
    apply3b = write_entry('{"ts":"2026-08-15T02:30:00Z","run":"3b"}')
    apply3b()
    ok3b = history.commit_and_push("chore: PAT 失效時的紀錄", ["history", "STATUS.md"],
                                   rewrite=apply3b)
    case("自動改用 GITHUB_TOKEN 後提交成功", ok3b)
    case("仍然是 GraphQL 簽章路徑（Verified 沒有掉）", len(COMMITS) == before + 1,
         f"COMMITS 增加 {len(COMMITS) - before}")
    case("使用的是 GITHUB_TOKEN", COMMITS[-1].get("token") == "ghs_fake_actions_token",
         str(COMMITS[-1].get("token")))
    MODE["fail_pat_403"] = False
    os.environ.pop("GITHUB_TOKEN", None)

    # ── ④ 全部 token 都不行 → 退回 git push ──
    print("\n④ 所有 token 都沒有 Contents 權限")
    MODE["fail_403"] = True
    before = len(COMMITS)
    apply3 = write_entry('{"ts":"2026-08-15T03:00:00Z","run":3}')
    apply3()
    ok3 = history.commit_and_push("chore: 第三筆紀錄", ["history", "STATUS.md"], rewrite=apply3)
    MODE["fail_403"] = False
    case("備援的 git push 成功（紀錄沒掉）", ok3)
    case("沒有透過 GraphQL 建立 commit", len(COMMITS) == before)
    remote_files = git("ls-tree", "-r", "--name-only", "main", cwd=BARE)
    case("遠端確實收到檔案", "history/2026-08.jsonl" in remote_files, remote_files)
    git("fetch", "origin", "main", cwd=SERVER_CLONE)
    git("reset", "--hard", "origin/main", cwd=SERVER_CLONE)
    case("第三筆紀錄真的推上遠端了",
         '"run":3' in (SERVER_CLONE / "history/2026-08.jsonl").read_text(encoding="utf-8"))

    print("\n" + "═" * 62)
    print("遠端 commit 歷史：")
    for line in git("log", "--oneline", "main", cwd=BARE).splitlines():
        print("  " + line)
    print("═" * 62)
    print("✅ 提交機制測試全數通過" if not bad else f"❌ 有 {bad} 項沒通過")

    server.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
