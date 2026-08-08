"""
抖音下载器 · 像素级对比飞轮 (Pixel Diff Flywheel)
=================================================
一键运行对比循环：
  1. 从 Pencil 导出设计稿 HTML → Playwright 截图（设计稿基准）
  2. 启动/连接 Flask → Playwright 截图实装页面
  3. 运行 _pixel_diff.py 计算 SSIM + 差异热力图 + 区域定位
  4. 输出量化报告，指导下一轮修复

用法：
  python run_pixel_flywheel.py            # 完整飞轮
  python run_pixel_flywheel.py --no-design # 跳过设计稿导出（已有基准图时）

依赖：
  pip install playwright pillow numpy scipy
  pencil mcp (export_html)
"""
import argparse
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
PENCIL_PEN = ROOT / "design" / "douyin-downloader-v4.pen"
DESIGN_HTML = ROOT / "_design_export.html"
DESIGN_SHOT = ROOT / "_design_screenshot.png"
IMPL_SHOT = ROOT / "_impl_screenshot.png"
FLASK_PORT = 5091
VIEWPORT = {"width": 1060, "height": 760}


def flask_running() -> bool:
    s = socket.socket()
    try:
        s.connect(("127.0.0.1", FLASK_PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def start_flask():
    if flask_running():
        print("  ✓ Flask 已在运行")
        return
    print("  → 启动 Flask 后端...")
    subprocess.Popen(
        [sys.executable, "app_server.py"],
        cwd=str(ROOT),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    # 等待就绪
    import time
    for _ in range(20):
        time.sleep(0.5)
        if flask_running():
            print("  ✓ Flask 就绪")
            return
    print("  ⚠ Flask 启动超时，请手动检查")


def capture_design(returncode_only=True):
    """调用 Pencil export_html（需 MCP 环境），然后 Playwright 截图"""
    print("\n[步骤 1/3] 生成设计稿基准图...")
    print(f"  设计稿 Pencil: {PENCIL_PEN}")
    print(f"  导出 HTML:     {DESIGN_HTML}")
    print("  ⚠ 此步骤需要通过 Pencil MCP 调用 export_html 导出 HTML。")
    print("  在 CodeBuddy 中，请使用 pencil.export_html 工具导出后继续。")
    print("  或重新运行本脚本时跳过此步：python run_pixel_flywheel.py --no-design")
    print("  如果基准图已存在，直接运行 --no-design 模式。")
    
    if DESIGN_HTML.exists() and DESIGN_SHOT.exists():
        print(f"  ✓ 已有基准图 {DESIGN_SHOT}")
        return True
    return False


def capture_impl():
    print("\n[步骤 2/3] 截图实装页面...")
    from playwright.sync_api import sync_playwright
    start_flask()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(f"http://127.0.0.1:{FLASK_PORT}", wait_until="load", timeout=10000)
        page.wait_for_timeout(2000)
        # 触发重渲染确保最新 CSS
        page.reload(wait_until="load")
        page.wait_for_timeout(1500)
        page.screenshot(path=str(IMPL_SHOT), full_page=False)
        browser.close()
    print(f"  ✓ 实装截图: {IMPL_SHOT}")


def run_diff():
    print("\n[步骤 3/3] 运行像素级 diff 引擎...")
    from importlib import import_module
    sys.path.insert(0, str(ROOT))
    # 直接调用 _pixel_diff 的 main
    import _pixel_diff
    _pixel_diff.main()
    print(f"\n📊 对比报告:")
    print(f"  差异热力图: {ROOT / '_diff_overlay.png'}")
    print(f"  量化指标:   {ROOT / '_diff_stats.json'}")
    print(f"  区域定位:   {ROOT / '_diff_regions.json'}")


def main():
    parser = argparse.ArgumentParser(description="抖音下载器像素级对比飞轮")
    parser.add_argument("--no-design", action="store_true",
                        help="跳过设计稿导出步骤（使用已有基准图）")
    args = parser.parse_args()

    print("=" * 60)
    print("  🎯 抖音下载器 · 像素级对比飞轮")
    print("=" * 60)

    if not args.no_design and not (DESIGN_SHOT.exists()):
        ok = capture_design()
        if not ok:
            print("\n⚠ 设计稿基准图缺失，请在 Pencil MCP 中执行：")
            print(f'   export_html(filePath="{PENCIL_PEN}", nodeIds=["page-download"], '
                  f'outputPath="{DESIGN_HTML}")')
            print("  然后重新运行本脚本。")
            return

    capture_impl()
    run_diff()

    print("\n" + "=" * 60)
    print("  飞轮循环完成。查看热力图定位差异区域，修复后重跑本脚本。")
    print("=" * 60)


if __name__ == "__main__":
    main()
