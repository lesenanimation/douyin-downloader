"""双端截图：设计稿(Pencil导出HTML) + 实装(Flask)，统一 1060x760 视口。"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
DESIGN_HTML = ROOT / "_design_export.html"
IMPL_URL = "http://127.0.0.1:5091"
W, H = 1060, 760


def shoot(page, target, out, is_file, demo=False):
    url = target.as_uri() if is_file else target
    if demo and not is_file:
        url += ("&" if "?" in url else "?") + "demo=1"
    page.goto(url, wait_until="load", timeout=20000)
    page.wait_for_timeout(2500)  # 等 demo 数据填充完
    page.screenshot(path=str(out), full_page=False)
    print(f"  -> {out.name}")


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else "both"
    demo = "demo" in sys.argv
    with sync_playwright() as p:
        b = p.chromium.launch(headless=True)
        pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
        if only in ("both", "design"):
            if not DESIGN_HTML.exists():
                print("  !! _design_export.html 不存在，请先用 Pencil export_html 导出")
            else:
                shoot(pg, DESIGN_HTML, ROOT / "_design_screenshot.png", True)
        if only in ("both", "impl"):
            shoot(pg, IMPL_URL, ROOT / "_impl_screenshot.png", False, demo=demo)
        b.close()


if __name__ == "__main__":
    main()
