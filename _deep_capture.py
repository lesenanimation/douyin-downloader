"""抓取 TOP 3 差异区域内所有子元素的 computed style + bounding box"""
import json
from playwright.sync_api import sync_playwright

# 需要深度检查的选择器列表
DEEP_SELECTORS = {
    # Toolbar 子元素
    'toolbar_btn_open': '.toolbar-row .btn:nth-child(1)',
    'toolbar_btn_failed': '.toolbar-row .btn:nth-child(2)',
    'toolbar_btn_log': '.toolbar-row .btn:nth-child(3)',
    # Config Row 子元素
    'config_label': '.config-row label',
    'config_input_box': '.config-input',
    'config_input_text': '#configPathText',
    'config_browse_btn': '#btnBrowseConfig',
    # Action Bar 子元素
    'cta_btn': '.btn-download-new',
    'cta_icon': '.btn-download-new .play-icon',
    'cta_text': '#btnDownloadText',
    'listen_group': '.listen-group',
    'listen_dot': '#monitorDot',
    'listen_label_interval': '.listen-group label',
    'listen_input': '#monitorInterval',
    'listen_unit': '.listen-group span[style*="font-size"]',
    'listen_btn': '#btnMonitor',
    'listen_status': '#monitorText',
    # Header 子元素
    'header_icon': '.dl-header-icon',
    'header_title': '.dl-header-title',
    'header_sub': '.dl-header-sub',
    # Status Bar 子元素
    'status_badge': '#statusBadge',
    'status_cookie_text': '#cookieText',
    'status_fetch_btn': '#btnFetch',
    'status_right': '.status-right',
}

STYLE_KEYS = [
    "width", "height", "backgroundColor", "color",
    "fontSize", "fontWeight", "fontFamily",
    "padding", "margin", "gap",
    "display", "alignItems", "justifyContent",
    "borderRadius", "border", "borderColor",
    "letterSpacing", "lineHeight",
    "opacity", "visibility",
]

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1060, "height": 760})
        page.goto("http://127.0.0.1:5091", wait_until="load", timeout=10000)
        page.wait_for_timeout(2000)

        results = {}
        for name, sel in DEEP_SELECTORS.items():
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
                            fontSize: cs.fontSize, fontWeight: cs.fontWeight,
                            fontFamily: cs.fontFamily,
                            padding: cs.padding, margin: cs.margin,
                            gap: cs.gap,
                            display: cs.display,
                            alignItems: cs.alignItems,
                            justifyContent: cs.justifyContent,
                            borderRadius: cs.borderRadius,
                            border: cs.border,
                            borderColor: cs.borderColor,
                            letterSpacing: cs.letterSpacing,
                            lineHeight: cs.lineHeight,
                            opacity: cs.opacity,
                        };
                    }""",
                    sel,
                )
                results[name] = {"box": box, "style": style}
                w = box["width"] if box else 0
                h = box["height"] if box else 0
                x = box["x"] if box else 0
                y = box["y"] if box else 0
                bg = style.get("backgroundColor", "?") if style else "?"
                print(f"  OK {name:25s} | {w:.0f}x{h:.0f} @ ({x:.0f},{y:.0f}) | bg={bg}")
            else:
                results[name] = None
                print(f"  MISS {name:25s}")

        out = r"E:\douyin-downloader\_deep_styles.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        browser.close()
        print(f"\nSaved to {out}")


if __name__ == "__main__":
    main()
