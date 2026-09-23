#!/usr/bin/env python
"""pre-commit 密钥扫描 —— 只读暂存区，命中即阻止提交。

设计要点：
- 只扫描「本次提交真正会入库的内容」（`git diff --cached` + `git show :path`），
  不碰工作区未暂存的改动。
- 黑名单只存 **SHA-256**，不存明文密钥，避免钩子自身成为泄露源。
- 输出时对命中的敏感串做脱敏（首 6 + 尾 4）。
- 误报可在该行加 `secret-scan:ignore`；整体跳过用 `git commit --no-verify`。

退出码：0 = 放行；1 = 阻止。
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import sys

IGNORE_MARKERS = ("secret-scan:ignore", "pragma: allowlist secret", "gitleaks:allow")

# 高置信密钥模式（尽量只留高信号，避免误报刷屏）
RULES = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("ssh private key", re.compile(r"-----BEGIN OPENSSH PRIVATE KEY-----")),  # secret-scan:ignore
    ("openai/deepseek key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("github token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("slack token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("aws access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("google api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("stripe key", re.compile(r"\bsk_(live|test)_[A-Za-z0-9]{16,}")),
    ("app/client secret", re.compile(
        r"(?i)\b(app[_-]?secret|client[_-]?secret|secret[_-]?key)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}")),
    ("token/password", re.compile(
        r"(?i)\b(access[_-]?token|auth[_-]?token|api[_-]?key|apikey|password|passwd)\b\s*[:=]\s*['\"][^'\"]{16,}['\"]")),
    ("bearer header", re.compile(
        r"(?i)authorization['\"]?\s*[:=]\s*['\"]?bearer\s+[A-Za-z0-9._\-]{16,}")),
    ("tmdb api key", re.compile(r"api_key=[0-9a-f]{32}")),
]

# 敏感文件名（即使内容模式没命中，也不该入库）
BAD_FILENAMES = re.compile(
    r"(?i)(^|/)("
    r"\.env|\.env\..*|"
    r"id_rsa.*|id_ed25519.*|.*\.pem|.*\.key|.*\.p12|.*\.pfx|"
    r"cookies?\.txt|config-\S*\.json|credentials\.json|secrets?\.(json|ya?ml|txt)"
    r")$"
)

# 已知泄露值的 SHA-256（只存哈希）
KNOWN_BAD_SHA256 = {
    "f0d60b7522508476594e5d9a4700f9c264d9ad8da6fc3f9e7b14b4704a74a667",
    "c06d33ca9f317a567143934f6b47b336fe26cab3e0653267a6de179ddb871230",
    "641f6063ec28ccb6d4851aaaf41a45387a9ac57eda18d8e36234be78a5c0fb86",
}
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")


def staged_paths() -> list[str]:
    r = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM", "-z"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return [p for p in (r.stdout or "").split("\0") if p]


def staged_bytes(path: str) -> bytes:
    r = subprocess.run(["git", "show", f":{path}"], capture_output=True)
    return r.stdout or b""


def redact(s: str) -> str:
    if len(s) <= 14:
        return s[:4] + "***"
    return s[:6] + "…" + s[-4:]


def scan_file(path: str) -> list[tuple[int, str, str]]:
    data = staged_bytes(path)
    if not data or b"\x00" in data[:4096]:
        return []
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return []
    hits: list[tuple[int, str, str]] = []
    for i, line in enumerate(text.splitlines(), 1):
        if any(mk in line for mk in IGNORE_MARKERS):
            continue
        for name, rx in RULES:
            m = rx.search(line)
            if m:
                hits.append((i, name, redact(m.group(0))))
        for tok in TOKEN_RE.findall(line):
            if hashlib.sha256(tok.encode()).hexdigest() in KNOWN_BAD_SHA256:
                hits.append((i, "known leaked secret", redact(tok)))
    return hits


def main() -> int:
    problems: list[tuple[str, int, str, str]] = []
    for p in staged_paths():
        if BAD_FILENAMES.search(p.replace("\\", "/")):
            problems.append((p, 0, "敏感文件名", p))
        for ln, kind, val in scan_file(p):
            problems.append((p, ln, kind, val))

    if not problems:
        return 0

    print("[secret-scan] 提交已阻止 —— 检测到疑似密码/密钥：", file=sys.stderr)
    for p, ln, kind, val in problems:
        loc = f"{p}:{ln}" if ln else p
        print(f"  {loc}  [{kind}]  {val}", file=sys.stderr)
    print("", file=sys.stderr)
    print("  ・误报：在该行行尾加注释  secret-scan:ignore", file=sys.stderr)
    print("  ・确认安全仍要提交：git commit --no-verify", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
