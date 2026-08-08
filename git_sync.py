"""仓库同步独立模块 — 安全版。

供 app_server 的 /api/git/* 路由共用。
所有 git 命令以当前文件所在目录为工作目录执行。

同步策略（无痛、不断运行）：
  1. fetch upstream  — 获取官方最新代码
  2. 若无更新 → 直接返回
  3. 若有本地未提交改动 → stash 暂存
  4. rebase upstream/main — 本地代码接在官方最新之上
  5. stash pop — 恢复本地改动
  6. 全程不 add / 不 commit / 不 push
"""
from __future__ import annotations

import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Callable, Optional

REPO_DIR = Path(__file__).resolve().parent
MACHINE_NAME = platform.node()
UPSTREAM_REMOTE = "upstream"  # 官方源，不是用户 fork
UPSTREAM_BRANCH = "upstream/main"

_LAST_SYNC_FILE = REPO_DIR / ".last_sync"


def _run(args: list[str], timeout: float = 120.0) -> tuple[int, str, str]:
    """跑 git 命令，返回 (returncode, stdout, stderr)。"""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(REPO_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "git executable not found in PATH"
    except subprocess.TimeoutExpired:
        return 124, "", f"git {args[0]} timed out after {timeout}s"
    except Exception as exc:
        return 1, "", f"{type(exc).__name__}: {exc}"


def get_status() -> dict:
    """返回当前仓库的同步状态。"""
    info: dict = {
        "repo_dir": str(REPO_DIR),
        "has_git": False,
        "has_upstream": False,
        "branch": "",
        "remote_url": "",
        "upstream_url": "",
        "ahead": 0,
        "behind": 0,
        "dirty": False,
        "dirty_files": 0,
        "last_commit": "",
        "last_sync": "",
        "machine": MACHINE_NAME,
    }
    if not (REPO_DIR / ".git").exists():
        info["error"] = ".git not found"
        return info
    info["has_git"] = True

    rc, out, _ = _run(["rev-parse", "--abbrev-ref", "HEAD"])
    if rc == 0:
        info["branch"] = out

    rc, out, _ = _run(["remote", "get-url", "origin"])
    if rc == 0:
        info["remote_url"] = out

    rc, out, _ = _run(["remote", "get-url", UPSTREAM_REMOTE])
    if rc == 0:
        info["has_upstream"] = True
        info["upstream_url"] = out

    rc, out, _ = _run(["status", "--porcelain"])
    if rc == 0:
        lines = [ln for ln in out.splitlines() if ln.strip()]
        info["dirty"] = len(lines) > 0
        info["dirty_files"] = len(lines)

    # 与 upstream 比较（而非 origin）
    if info["has_upstream"]:
        rc, out, _ = _run(
            ["rev-list", "--left-right", "--count", f"HEAD...{UPSTREAM_BRANCH}"]
        )
        if rc == 0 and out:
            parts = out.split()
            if len(parts) == 2:
                info["ahead"] = int(parts[0])
                info["behind"] = int(parts[1])

    rc, out, _ = _run(["log", "-1", "--pretty=format:%h %s (%cr)"])
    if rc == 0:
        info["last_commit"] = out

    if _LAST_SYNC_FILE.exists():
        try:
            info["last_sync"] = _LAST_SYNC_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass

    return info


def run_sync(
    fetch_only: bool = False,
    log_callback: Optional[Callable[[str, str], None]] = None,
) -> dict:
    """执行安全同步流程。

    流程：
      1. fetch upstream
      2. 若无更新 → 结束
      3. 若有本地改动 → stash
      4. rebase upstream/main
      5. stash pop（恢复本地改动）
      6. 全程不 add / commit / push
    """
    def _emit(level: str, msg: str) -> None:
        if log_callback:
            try:
                log_callback(level, msg)
            except Exception:
                pass

    summary: dict = {"success": False, "steps": [], "fetch_only": fetch_only}

    if not (REPO_DIR / ".git").exists():
        _emit("err", ".git 不存在，无法同步")
        summary["error"] = ".git not found"
        return summary

    # 检查 upstream 远端是否存在
    rc, _, _ = _run(["remote", "get-url", UPSTREAM_REMOTE])
    if rc != 0:
        _emit(
            "err",
            f"未配置 upstream 远端。请运行: git remote add upstream <官方仓库地址>",
        )
        summary["error"] = "upstream remote not configured"
        return summary

    def _step(
        label: str, args: list[str], timeout: float = 180.0, expect_zero: bool = True
    ) -> tuple[bool, str, str]:
        _emit("info", f'$ git {" ".join(args)}')
        rc, out, err = _run(args, timeout=timeout)
        ok = (rc == 0) if expect_zero else True
        if out:
            for ln in out.splitlines():
                _emit("info", "  " + ln)
        if err:
            level = "warn" if ok else "err"
            for ln in err.splitlines():
                _emit(level, "  " + ln)
        summary["steps"].append(
            {
                "label": label,
                "cmd": "git " + " ".join(args),
                "rc": rc,
                "ok": ok,
            }
        )
        if not ok:
            _emit("err", f"[{label}] 失败 (rc={rc})")
        return ok, out, err

    # ── 1) fetch upstream ──
    ok, _, _ = _step("fetch", ["fetch", UPSTREAM_REMOTE], timeout=120.0)
    if not ok:
        summary["error"] = "fetch failed"
        return summary

    if fetch_only:
        _emit("ok", "仅 fetch 完成（未合并）")
        _write_last_sync("fetch_only")
        summary["success"] = True
        return summary

    # ── 2) 检查是否有更新 ──
    rc, behind_str, _ = _run(
        ["rev-list", "--count", f"HEAD..{UPSTREAM_BRANCH}"]
    )
    behind = 0
    try:
        behind = int(behind_str.strip()) if behind_str.strip() else 0
    except ValueError:
        pass

    if behind == 0:
        _emit("ok", "已是最新版本，无需更新")
        _write_last_sync("up-to-date")
        summary["success"] = True
        return summary

    _emit("info", f"发现 {behind} 个官方更新，准备同步...")

    # ── 3) 暂存本地改动 ──
    rc, dirty_out, _ = _run(["status", "--porcelain"])
    has_local_changes = rc == 0 and bool(dirty_out.strip())
    stashed = False

    if has_local_changes:
        _emit("info", f"检测到本地未提交改动，暂存后同步...")
        ok, _, _ = _step(
            "stash", ["stash", "push", "-m", f"auto-sync stash {int(time.time())}"]
        )
        if not ok:
            _emit("warn", "暂存失败，尝试直接同步（可能因本地改动而失败）")
        else:
            stashed = True

    # ── 4) rebase onto upstream/main ──
    rebase_ok = False
    try:
        ok, out, err = _step("rebase", ["rebase", UPSTREAM_BRANCH])
        rebase_ok = ok
    except Exception:
        pass

    if not rebase_ok:
        # rebase 失败，中止并恢复
        _emit("err", "rebase 失败，正在回滚...")
        _step("rebase-abort", ["rebase", "--abort"])
        if stashed:
            _step("stash-pop", ["stash", "pop"])
        summary["error"] = "rebase conflict"
        summary["conflict"] = True
        return summary

    _emit("ok", f"已同步 {behind} 个官方更新")

    # ── 5) 恢复本地改动 ──
    if stashed:
        ok, out, err = _step("stash-pop", ["stash", "pop"])
        if not ok:
            _emit(
                "warn",
                "本地改动恢复时有冲突，请手动处理 (git stash list 查看暂存)",
            )
            summary["stash_conflict"] = True
        else:
            _emit("ok", "本地改动已恢复")

    _write_last_sync("synced")
    summary["success"] = True
    return summary


def _write_last_sync(tag: str = "") -> None:
    """写入最后同步时间戳。"""
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    suffix = f" ({tag})" if tag else ""
    _LAST_SYNC_FILE.write_text(
        f"{ts}{suffix} [{MACHINE_NAME}]", encoding="utf-8"
    )


if __name__ == "__main__":
    import sys as _sys
    import json as _json

    def _print(level: str, msg: str) -> None:
        prefix = {"info": "·", "ok": "✓", "warn": "!", "err": "✗"}.get(level, "·")
        print(f"{prefix} {msg}", flush=True)

    if len(_sys.argv) > 1 and _sys.argv[1] == "status":
        print(_json.dumps(get_status(), ensure_ascii=False, indent=2))
    else:
        fetch_only = "--fetch-only" in _sys.argv
        result = run_sync(fetch_only=fetch_only, log_callback=_print)
        _sys.exit(0 if result.get("success") else 1)
