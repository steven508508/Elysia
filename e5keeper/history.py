"""執行歷史：寫成 history/YYYY-MM.jsonl，並更新 STATUS.md，然後 commit 回 repo。

順帶好處：repo 持續有 commit，GitHub 就不會因為「60 天沒有活動」而自動停用排程。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from .runner import RunReport
from .utils import fmt_duration, fmt_time, log, mask_email, now_local, scrub_public

ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = ROOT / "history"
STATUS_FILE = ROOT / "STATUS.md"
KEEP_ROWS = 20


# ══════════════════ 紀錄 ══════════════════

def record(settings, report: RunReport, *, push: bool = True) -> Path | None:
    if not settings.features.get("history"):
        return None

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    path = HISTORY_DIR / f"{now:%Y-%m}.jsonl"

    # history/ 與 STATUS.md 會 commit 進 repo，公開的話等於永久公開，所以一律清洗。
    # trigger 也要洗 —— Telegram 指令會把觸發者的使用者名稱帶進來。
    def clean(text: str, account=None) -> str:
        extra = ()
        if account is not None:
            extra = tuple(x for x in (account.email,) if x)
        return scrub_public(text, extra)

    def clean_trigger(text: str) -> str:
        """Telegram 觸發的紀錄會帶上「誰下的指令」，那是你的 Telegram 使用者名稱。
        送到私訊沒問題，但不能 commit 進公開 repo —— 一年下來等於公開你的
        Telegram 帳號、以及你每次手動操作的精確時間。"""
        return scrub_public(re.sub(r"\s*\([^)]*\)\s*$", "", str(text or "")).strip())

    # minimal（預設）= 只留維運上真正需要的：別名與成功率。
    #   刻意不寫入 email（連遮蔽版都不寫 —— 網域是完整的，光靠網域就能查出你的
    #   租用戶 ID，再配上局部的帳號名稱就足以列舉）、也不寫訂閱剩餘天數與 SKU
    #   （那等於公開你的訂閱到期日，剛好是最適合拿來挑時機的資訊）。
    # full = 全部寫入。repo 是私有的、或你自己想留完整趨勢時再用。
    full_detail = settings.features.get("history_detail", "minimal") == "full"

    def account_entry(a) -> dict:
        item = {
            "alias": a.alias,
            "token_ok": a.token_ok,
            "rotated": a.rotated,
            "ok": a.ok_count,
            "warn": a.warn_count,
            "fail": a.fail_count,
            "total": a.total_count,
            "duration": round(a.duration, 1),
            "token_error": clean(a.token_error, a)[:200],
            "fails": [
                {"id": r.spec_id, "status": r.status, "error": clean(r.error, a)[:120]}
                for r in a.results
                if not r.counts_as_success
            ],
        }
        if full_detail:
            item["email"] = mask_email(a.email, settings.notify.get("mask_email", True))
            item["days_left"] = a.subscription.days_left if a.subscription else None
            item["sku"] = a.subscription.sku if a.subscription else ""
        return item

    entry = {
        "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": report.mode,
        "trigger": clean_trigger(report.trigger),
        "duration": round(report.duration, 1),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "accounts": [account_entry(a) for a in report.accounts],
    }

    line = json.dumps(entry, ensure_ascii=False)

    def apply() -> None:
        """把這次紀錄寫進檔案。設計成可重複呼叫（rebase 衝突時要重跑一次）。"""
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if line not in existing:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        _write_status(settings, report)

    apply()
    log(f"已寫入歷史紀錄 {path.name}", level="ok")

    if push:
        commit_and_push(
            f"chore(e5keeper): {report.mode} 執行紀錄 {now:%Y-%m-%d %H:%M} UTC",
            ["history", "STATUS.md"],
            rewrite=apply,
        )
    return path


def _public_trigger(text: str) -> str:
    """同上：STATUS.md 也會 commit 進 repo。"""
    return scrub_public(re.sub(r"\s*\([^)]*\)\s*$", "", str(text or "")).strip())


def _write_status(settings, report: RunReport) -> None:
    tz = settings.tz
    ok, warn, fail, total = report.totals
    dead = sum(1 for a in report.accounts if not a.token_ok)
    if report.all_healthy:
        icon = "🟢 正常"
    elif dead:
        icon = f"🚫 有 {dead} 個帳號無法登入"
    elif fail:
        icon = "🔴 有失敗"
    else:
        icon = "🟡 有警告"

    lines = [
        "# E5 保活精靈 · 目前狀態",
        "",
        f"> 最後更新：**{fmt_time(now_local(tz))}**（{tz}）　狀態：**{icon}**",
        "",
        f"- 執行模式：`{report.mode}`　觸發：`{_public_trigger(report.trigger)}`",
        f"- 本輪 API：{ok} ✅ ／ {warn} ⚠️ ／ {fail} ❌（共 {total}）",
        f"- 總耗時：{fmt_duration(report.duration)}",
        "",
        "## 各帳號",
        "",
    ]
    # STATUS.md 也會 commit 進 repo，所以同樣受 history_detail 控制
    full_detail = settings.features.get("history_detail", "minimal") == "full"
    if full_detail:
        lines += ["| 狀態 | 帳號 | 認證 | ✅ | ⚠️ | ❌ | 訂閱剩餘 | 耗時 |",
                  "|:--:|---|---|--:|--:|--:|--:|--:|"]
    else:
        lines += ["| 狀態 | 帳號 | 認證 | ✅ | ⚠️ | ❌ | 耗時 |",
                  "|:--:|---|---|--:|--:|--:|--:|"]

    for a in report.accounts:
        mode_txt = "委派" if a.mode == "delegated" else "應用程式"
        alias = a.alias if a.token_ok else f"{a.alias} ⚠️需重新授權"
        row = (f"| {a.status_icon} | {alias} | {mode_txt} | {a.ok_count} | "
               f"{a.warn_count} | {a.fail_count} | ")
        if full_detail:
            days = a.subscription.days_left if a.subscription else None
            row += f"{f'{days} 天' if days is not None else '—'} | "
        lines.append(row + f"{fmt_duration(a.duration)} |")

    recent = _recent_entries(KEEP_ROWS)
    if recent:
        lines += ["", "## 最近執行", "", "| 時間 (UTC) | 模式 | ✅ | ⚠️ | ❌ |", "|---|---|--:|--:|--:|"]
        for item in recent:
            accs = item.get("accounts") or []
            lines.append(
                f"| {item.get('ts', '')} | {item.get('mode', '')} | "
                f"{sum(a.get('ok', 0) for a in accs)} | "
                f"{sum(a.get('warn', 0) for a in accs)} | "
                f"{sum(a.get('fail', 0) for a in accs)} |"
            )

    lines += ["", "---", "", "<sub>本檔案由 E5 保活精靈自動產生，請勿手動編輯。</sub>", ""]
    STATUS_FILE.write_text("\n".join(lines), encoding="utf-8")


def _recent_entries(limit: int) -> list[dict]:
    if not HISTORY_DIR.exists():
        return []
    files = sorted(HISTORY_DIR.glob("*.jsonl"))
    rows: list[dict] = []
    for path in reversed(files):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in reversed(lines):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rows.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
            if len(rows) >= limit:
                return rows
    return rows


def load_entries(days: int = 7) -> list[dict]:
    """讀出最近 N 天的紀錄，給週報用。"""
    from .utils import parse_iso

    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    out: list[dict] = []
    if not HISTORY_DIR.exists():
        return out
    for path in sorted(HISTORY_DIR.glob("*.jsonl")):
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            dt = parse_iso(item.get("ts", ""))
            if dt and dt.timestamp() >= cutoff:
                out.append(item)
    return out


# ══════════════════ Git ══════════════════

def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=ROOT, check=check,
        capture_output=True, text=True, timeout=120,
    )


def commit_and_push(
    message: str,
    paths: list[str] | None = None,
    *,
    rewrite=None,
    attempts: int = 3,
    signed: bool = True,
) -> bool:
    """把檔案提交回 repo。只在 GitHub Actions 環境中執行。

    預設走 GitHub GraphQL API，這樣 commit 會由 GitHub 自動簽章、顯示 ✅ Verified。
    API 這條路走不通（權限不足、服務異常）時，自動退回一般的 git push ——
    紀錄照樣留得住，只是沒有 Verified 標記。
    """
    if not os.environ.get("GITHUB_ACTIONS"):
        log("非 Actions 環境，略過 commit")
        return False

    targets = paths or ["history", "STATUS.md", "state"]
    branch = os.environ.get("GITHUB_REF_NAME", "") or _current_branch()
    # 用 PAT 建立的 commit 會被視為「你本人」推的，可能觸發 on:push 類的 workflow，
    # 所以統一加上 [skip ci]
    full = f"{message} [skip ci]"

    if signed:
        ok, fell_back = _commit_signed(full, targets, branch, rewrite, attempts)
        if ok or not fell_back:
            return ok
        log("改用一般 git push 備援（commit 不會有 Verified 標記）", level="warn")

    return _commit_via_git(full, targets, branch, rewrite, attempts)


# ── 方式一：GraphQL 簽章提交（Verified）────────────────────────

def _commit_signed(
    message: str, targets: list[str], branch: str, rewrite, attempts: int
) -> tuple[bool, bool]:
    """回傳 (是否成功, 是否該退回 git push)。

    依序試每一個可用的 token。PAT 權限不足時改用 GITHUB_TOKEN，
    這樣至少還保得住 Verified 標記（只是作者變成 bot）。
    """
    from . import gitapi

    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    tokens = gitapi.candidate_tokens()
    if not repo or not tokens:
        log("沒有可用的 token 或 repo 資訊，無法建立簽章 commit", level="warn")
        return False, True

    for i, (token, source) in enumerate(tokens):
        if i:
            log(f"改用 {source} 再試一次簽章提交", level="warn")
        ok, fell_back = _commit_signed_with(
            token, source, repo, message, targets, branch, rewrite, attempts
        )
        if ok or not fell_back:
            return ok, fell_back
    return False, True


def _commit_signed_with(
    token: str, source: str, repo: str,
    message: str, targets: list[str], branch: str, rewrite, attempts: int,
) -> tuple[bool, bool]:
    from . import gitapi

    for attempt in range(1, attempts + 1):
        try:
            remote_oid = gitapi.get_head_oid(token, repo, branch)
        except gitapi.CommitError as exc:
            log(f"取得遠端 HEAD 失敗：{exc}", level="warn")
            return False, True

        # 本地落後遠端時，先同步再把這次的變更重新套上去，
        # 否則我們送出的檔案內容會蓋掉別人剛寫進去的紀錄。
        local_oid = (_git("rev-parse", "HEAD", check=False).stdout or "").strip()
        if local_oid and remote_oid != local_oid:
            log(f"遠端已更新（{local_oid[:7]} → {remote_oid[:7]}），重新同步後再套用本次變更")
            _git("fetch", "origin", branch, check=False)
            if _git("reset", "--hard", f"origin/{branch}", check=False).returncode != 0:
                log("同步遠端失敗", level="warn")
                return False, True
            if rewrite is not None:
                try:
                    rewrite()
                except Exception as exc:  # noqa: BLE001
                    log(f"重新產生檔案失敗：{exc}", level="warn")
                    return False, True

        changes = _changed_files(targets)
        if not changes:
            log("沒有變更需要提交")
            return False, False

        try:
            oid, url = gitapi.commit_files(token, repo, branch, message,
                                           remote_oid, ROOT, changes)
        except gitapi.CommitError as exc:
            if exc.fatal:
                log(f"簽章提交失敗（無法透過重試解決）：{exc}", level="warn")
                return False, True
            if exc.retryable and attempt < attempts:
                log(f"簽章提交失敗（第 {attempt}/{attempts} 次）：{exc}，重試中", level="warn")
                time.sleep(2 * attempt)
                continue
            log(f"簽章提交失敗：{exc}", level="warn")
            return False, True

        log(f"✅ Verified commit {oid[:7]} 已建立（身分：{source}）", level="ok")
        if url:
            log(f"   {url}")
        _sync_local(branch)
        return True, False

    return False, True


def _changed_files(targets: list[str]) -> list[tuple[str, str]]:
    """用 git status 找出變更的檔案，回傳 [(相對路徑, 'A' 或 'D'), ...]。"""
    existing = [t for t in targets if (ROOT / t).exists()]
    lookup = existing or targets
    result = _git("-c", "core.quotePath=false", "status", "--porcelain",
                  "--untracked-files=all", "--", *lookup, check=False)
    changes: list[tuple[str, str]] = []
    for line in (result.stdout or "").splitlines():
        if len(line) < 4:
            continue
        code, path = line[:2], line[3:].strip()
        if " -> " in path:                 # 改名：只取新路徑
            path = path.split(" -> ", 1)[1]
        path = path.strip('"')
        changes.append((path, "D" if "D" in code else "A"))
    return changes


def _sync_local(branch: str) -> None:
    """簽章 commit 是直接建在遠端的，本地要跟上，免得同一個 job 之後重複提交。"""
    _git("fetch", "origin", branch, check=False)
    _git("reset", "--hard", f"origin/{branch}", check=False)


# ── 方式二：一般 git push（備援，沒有 Verified）─────────────────

def _commit_via_git(
    message: str, targets: list[str], branch: str, rewrite, attempts: int
) -> bool:
    try:
        _git("config", "user.name", "e5-keeper[bot]")
        _git("config", "user.email", "e5-keeper[bot]@users.noreply.github.com")
    except Exception as exc:  # noqa: BLE001
        log(f"設定 git 身分失敗：{exc}", level="warn")
        return False

    for attempt in range(1, attempts + 1):
        existing = [t for t in targets if (ROOT / t).exists()]
        if not existing:
            return False

        _git("add", "--", *existing, check=False)
        status = _git("status", "--porcelain", "--", *existing, check=False)
        if not status.stdout.strip():
            log("沒有變更需要提交")
            return False

        commit = _git("commit", "-m", message, check=False)
        if commit.returncode != 0 and "nothing to commit" not in commit.stdout:
            log(f"git commit 失敗：{(commit.stderr or commit.stdout)[:200]}", level="warn")
            return False

        push = _git("push", "origin", f"HEAD:{branch}", check=False)
        if push.returncode != 0:
            # actions/checkout v6 起，認證資訊改存到 $RUNNER_TEMP 而不是 .git/config。
            # 我們不想相依於它的內部做法，所以這裡自備一條路：
            # 用 credential helper 從環境變數讀 token —— token 不會出現在
            # 指令列（會被 ps 看到）也不會被寫進 .git/config。
            push = _push_with_token(branch) or push

        if push.returncode == 0:
            log("已提交回 repo", level="ok")
            return True

        log(f"git push 失敗（第 {attempt}/{attempts} 次）：{push.stderr[:200]}", level="warn")
        if attempt == attempts:
            break

        # 別人先推了 → 拉到最新，重新產生我們的檔案，再試一次
        _git("fetch", "origin", branch, check=False)
        reset = _git("reset", "--hard", f"origin/{branch}", check=False)
        if reset.returncode != 0:
            log(f"git reset 失敗：{reset.stderr[:200]}", level="warn")
            break
        if rewrite is not None:
            try:
                rewrite()
            except Exception as exc:  # noqa: BLE001
                log(f"重新產生檔案失敗：{exc}", level="warn")
                break
        time.sleep(2 * attempt)

    log("提交失敗，本次紀錄只保留在 Actions 日誌中", level="warn")
    return False


def _push_with_token(branch: str):
    """自備認證推送，不依賴 actions/checkout 幫我們留下的憑證。

    token 透過環境變數交給 credential helper，所以不會出現在指令列
    （`ps` 看得到）、也不會被寫進 .git/config（那會留在工作目錄裡）。
    """
    from . import gitapi

    token, source = gitapi.pick_token()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not token or not repo:
        return None

    log(f"改用 {source} 自備認證重試推送", level="warn")
    env = dict(os.environ, GIT_PUSH_TOKEN=token)
    helper = ('!f() { echo username=x-access-token; '
              'echo password="$GIT_PUSH_TOKEN"; }; f')
    try:
        return subprocess.run(
            ["git", "-c", f"credential.helper={helper}",
             "push", f"https://github.com/{repo}", f"HEAD:{branch}"],
            cwd=ROOT, env=env, check=False,
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:  # noqa: BLE001
        log(f"自備認證推送失敗：{exc.__class__.__name__}", level="warn")
        return None


def _current_branch() -> str:
    result = _git("rev-parse", "--abbrev-ref", "HEAD", check=False)
    name = (result.stdout or "").strip()
    return name if name and name != "HEAD" else "main"
