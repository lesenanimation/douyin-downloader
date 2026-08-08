"""抓取实装页面所有关键 UI 组件的 computed style + bounding box"""
import json
from playwright.sync_api import sync_playwright

SELECTORS = {
    'header': '.dl-header',
    'header_title': '#dlHeaderTitle',
    'header_sub': '#dlHeaderSub',
    'config_row': '.config-row',
    'config_input': '.config-input',
    'action_bar': '.action-bar-new',
    'cta_btn': '.btn-download-new',
    'listen_group': '.listen-group',
    'toolbar': '.toolbar-row',
    'cookie_banner': '.cookie-warn-banner',
    'status_bar': '.status-bar-new',
    'sidebar': '.sidebar-dl',
}

STYLE_KEYS = [
    "width", "height", "backgroundColor", "color",
    "fontSize", "fontWeight", "fontFamily",
    "padding", "margin", "gap", "display",
    "alignItems", "justifyContent",
    "borderRadius", "border", "letterSpacing",
]

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1060, "height": 760})
        page.goto("http://127.0.0.1:5091", wait_until="load", timeout=10000)
        page.wait_for_timeout(2000)

        results = {}
        for name, sel in SELECTORS.items():
            el = page.query_selector(sel)
            if el:
                box = el.bounding_box()
                style = page.evaluate(
                    """(sel) => {
                        const el = document.querySelector(sel);
                        if (!el) return null;
                        const cs = getComputedStyle(el);
                        return {
                            width: cs.width, height: cs.height,
                            backgroundColor: cs.backgroundColor,
                            color: cs.color,
                            fontSize: cs.fontSize,
                            fontWeight: cs.fontWeight,
                            fontFamily: cs.fontFamily,
                            padding: cs.padding,
                            margin: cs.margin,
                            gap: cs.gap,
                            display: cs.display,
                            alignItems: cs.alignItems,
                            justifyContent: cs.justifyContent,
                            borderRadius: cs.borderRadius,
                            border: cs.border,
                            letterSpacing: cs.letterSpacing,
                        };
                    }""",
                    sel,
                )
                results[name] = {"box": box, "style": style}
                w = box["width"] if box else 0
                h = box["height"] if box else 0
                x = box["x"] if box else 0
                y = box["y"] if box else 0
                print(f"  OK {name:20s} | {w:.0f}x{h:.0f} @ ({x:.0f},{y:.0f})")
            else:
                results[name] = None
                print(f"  MISS {name:20s}")

        out = r"E:\douyin-downloader\_impl_computed_styles.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        browser.close()
        print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
