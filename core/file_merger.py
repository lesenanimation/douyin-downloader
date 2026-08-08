"""文件合并查重 — 将多个源目录合并到输出目录，按 SHA256 内容哈希自动去重。

从 file-merge-dedupe PowerShell 工具移植到 Python。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set


def _hash_file(path: Path, chunk_size: int = 64 * 1024) -> str:
    """计算文件 SHA256 哈希。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _find_files(
    root: Path, extensions: Set[str], recurse: bool
) -> List[Path]:
    """递归或平层扫描目录，按扩展名过滤。"""
    files: List[Path] = []
    if recurse:
        for entry in root.rglob("*"):
            if entry.is_file():
                if not extensions or entry.suffix.lower() in extensions:
                    files.append(entry)
    else:
        for entry in root.iterdir():
            if entry.is_file():
                if not extensions or entry.suffix.lower() in extensions:
                    files.append(entry)
    return files


def _unique_dest(output_dir: Path, filename: str) -> Path:
    """确保输出文件名不冲突，冲突时加 _1 _2 后缀。"""
    candidate = output_dir / filename
    if not candidate.exists():
        return candidate

    stem = Path(filename).stem
    suffix = Path(filename).suffix
    index = 1
    while True:
        candidate = output_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def run_merge(
    sources: List[str],
    output: str,
    mode: str = "copy",
    extensions: Optional[List[str]] = None,
    recurse: bool = True,
    clean_output: bool = False,
    progress_callback: Optional[Callable[[str, dict], None]] = None,
) -> dict:
    """执行合并查重。

    Args:
        sources: 源目录路径列表
        output: 输出目录
        mode: "copy" 或 "move"
        extensions: 扩展名过滤列表，如 [".mp4", ".jpg"]，空列表表示全部
        recurse: 是否递归扫描子目录
        clean_output: 是否清空输出目录
        progress_callback: 进度回调 (event, data)

    Returns:
        统计结果 dict
    """
    def _emit(event: str, data: dict = None) -> None:
        if progress_callback:
            try:
                progress_callback(event, data or {})
            except Exception:
                pass

    output_dir = Path(output).resolve()
    source_dirs = [Path(s).resolve() for s in sources]

    # 验证
    if len(set(source_dirs)) < len(source_dirs):
        return {"success": False, "error": "存在重复的源路径"}
    if output_dir in source_dirs:
        return {"success": False, "error": "输出路径不能和源路径相同"}
    for sd in source_dirs:
        if not sd.exists():
            return {"success": False, "error": f"源路径不存在: {sd}"}

    # 准备输出目录
    if clean_output and output_dir.exists():
        _emit("log", {"msg": f"清空输出目录: {output_dir}"})
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    ext_set = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in (extensions or [])}

    # 收集文件
    all_files: List[Path] = []
    for sd in source_dirs:
        files = _find_files(sd, ext_set, recurse)
        _emit("log", {"msg": f"扫描 {sd}: 找到 {len(files)} 个文件"})
        all_files.extend(files)

    total = len(all_files)
    _emit("progress", {"total": total, "current": 0, "phase": "scanning"})

    hash_index: Dict[str, Path] = {}  # hash → 输出路径
    stats = {
        "total_scanned": 0,
        "unique_saved": 0,
        "duplicates_skipped": 0,
        "renamed_on_conflict": 0,
        "errors": 0,
    }
    report_lines: List[str] = []
    errors: List[str] = []

    _emit("progress", {"total": total, "current": 0, "phase": "processing"})

    for i, file in enumerate(all_files):
        stats["total_scanned"] += 1
        file_hash: Optional[str] = None

        try:
            file_hash = _hash_file(file)
        except (OSError, PermissionError) as e:
            stats["errors"] += 1
            msg = f"无法读取: {file} ({e})"
            errors.append(msg)
            _emit("log", {"msg": msg, "level": "error"})
            continue

        # 去重检查
        if file_hash in hash_index:
            stats["duplicates_skipped"] += 1
            existing = hash_index[file_hash]
            _emit("log", {"msg": f"跳过重复: {file.name} (已存在 {existing.name})", "level": "info"})
            report_lines.append(f"{file_hash}\t{file}\t{existing}")
            continue

        # 确定目标路径
        dest = _unique_dest(output_dir, file.name)
        if dest.name != file.name:
            stats["renamed_on_conflict"] += 1

        try:
            if mode == "move":
                shutil.move(str(file), str(dest))
            else:
                shutil.copy2(str(file), str(dest))
        except (OSError, shutil.Error) as e:
            stats["errors"] += 1
            msg = f"操作失败: {file} → {dest} ({e})"
            errors.append(msg)
            _emit("log", {"msg": msg, "level": "error"})
            continue

        hash_index[file_hash] = dest
        stats["unique_saved"] += 1
        _emit("log", {"msg": f"保存: {dest.name}", "level": "success"})

        if i % 10 == 0 or i == total - 1:
            _emit("progress", {"total": total, "current": i + 1, "phase": "processing"})

    # 生成报告
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    report_path = output_dir / f"merge-dedupe-report-{timestamp}.txt"

    report_content = [
        "合并查重报告",
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"模式: {mode}",
    ]
    for idx, sd in enumerate(source_dirs):
        report_content.append(f"源路径 {idx + 1}: {sd}")
    report_content.append(f"输出路径: {output_dir}")
    report_content.append(f"递归: {recurse}")
    report_content.append(f"清空输出目录: {clean_output}")
    ext_display = ", ".join(sorted(ext_set)) if ext_set else "全部文件"
    report_content.append(f"扩展名过滤: {ext_display}")
    report_content.append("")
    report_content.append("统计：")
    report_content.append(f"- 扫描文件: {stats['total_scanned']}")
    report_content.append(f"- 保留唯一文件: {stats['unique_saved']}")
    report_content.append(f"- 跳过重复文件: {stats['duplicates_skipped']}")
    report_content.append(f"- 因重名改名: {stats['renamed_on_conflict']}")
    report_content.append(f"- 错误: {stats['errors']}")
    if errors:
        report_content.append("")
        report_content.append("错误详情：")
        report_content.extend(errors)
    report_content.append("")
    report_content.append("重复文件（SHA256 重复→已保留 目标）：")
    report_content.extend(report_lines)

    report_path.write_text("\n".join(report_content), encoding="utf-8")

    _emit("log", {"msg": f"报告: {report_path.name}", "level": "system"})
    _emit("done", stats)

    return {"success": True, "stats": stats, "report": str(report_path)}


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 4:
        print("用法: python file_merger.py <源A> <源B> <输出> [--move] [--ext .mp4,.jpg] [--clean]")
        sys.exit(1)

    sources = [sys.argv[1], sys.argv[2]]
    output = sys.argv[3]
    mode = "move" if "--move" in sys.argv else "copy"
    exts = None
    for arg in sys.argv:
        if arg.startswith("--ext="):
            exts = arg[6:].split(",")
    clean = "--clean" in sys.argv

    def _cb(event, data):
        if event == "log":
            prefix = {"error": "✗", "success": "✓", "system": "·"}.get(
                data.get("level", ""), " "
            )
            print(f"  {prefix} {data['msg']}")
        elif event == "progress":
            pct = data["current"] / max(data["total"], 1) * 100
            print(f"\r  [{data['phase']}] {data['current']}/{data['total']} ({pct:.0f}%)", end="")
        elif event == "done":
            print("\n")
            s = data
            print(f"扫描: {s['total_scanned']} | 保留: {s['unique_saved']} | "
                  f"跳过重复: {s['duplicates_skipped']} | 改名: {s['renamed_on_conflict']}")

    result = run_merge(sources, output, mode=mode, extensions=exts,
                       clean_output=clean, progress_callback=_cb)
    print(f"\n完成: {result}")
