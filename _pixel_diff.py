"""
像素级对比引擎 v1.0
====================
输入：设计稿截图(Pencil get_screenshot) vs 实装截图(Playwright)
输出：
  1. _diff_overlay.png   — 差异热力图（红=仅设计稿有, 蓝=仅实装有, 灰=都有但颜色不同）
  2. _diff_stats.json    — 量化指标（MSE, SSIM, 差异像素占比, 分块 bbox 列表）
  3. _diff_regions.json  — 差异区域坐标 + 面积 + 类型分类
"""
import json
import sys
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

# ── 配置 ──────────────────────────────────────────────
DESIGN_PATH = Path(__file__).parent / "_design_screenshot.png"
IMPL_PATH   = Path(__file__).parent / "_impl_screenshot.png"
OUT_DIFF    = Path(__file__).parent / "_diff_overlay.png"
OUT_STATS   = Path(__file__).parent / "_diff_stats.json"
OUT_REGIONS = Path(__file__).parent / "_diff_regions.json"

# 颜色阈值（0-255，越小越敏感）
COLOR_THRESHOLD = 30
# 最小差异区域面积（像素²），过滤噪点
MIN_REGION_AREA = 100

# ── 排除区域（动态内容/条件显隐，非渲染bug）──────────────
# 格式: (name, x1, y1, x2, y2)  — 绝对坐标（基于 1060×760 画布）
# 这些区域在"空状态 vs 示例数据"或"条件显隐"下必然不同，不参与 SSIM 计算
EXCLUDE_ZONES = [
    # 下载列表动态区域（设计稿有示例卡片，实装空状态）
    ("content_list", 220, 500, 1060, 760),
    # Cookie 警告横幅（条件显隐，取决于 cookie 状态）
    ("cookie_banner", 220, 312, 1060, 360),
    # 左侧边栏图标（emoji vs Pencil 内置图标，风格差异可接受）
    ("sidebar_icons", 0, 50, 70, 760),
]

def apply_exclude_mask(mask: np.ndarray, size: tuple) -> np.ndarray:
    """将排除区域从差异掩码中剔除"""
    w, h = size
    for name, x1, y1, x2, y2 in EXCLUDE_ZONES:
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 > x1 and y2 > y1:
            mask[y1:y2, x1:x2] = False
    return mask


def load_images(d_path: str, i_path: str) -> tuple:
    """加载并归一化两张图为相同尺寸 RGBA"""
    design = Image.open(d_path).convert("RGBA")
    impl   = Image.open(i_path).convert("RGBA")
    
    # 统一尺寸（取较大者，不足部分透明填充）
    w = max(design.width, impl.width)
    h = max(design.height, impl.height)
    
    d_resized = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    i_resized = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d_resized.paste(design, (0, 0))
    i_resized.paste(impl, (0, 0))
    
    return np.array(d_resized, dtype=np.float32), np.array(i_resized, dtype=np.float32), (w, h)


def compute_pixel_diff(d_arr: np.ndarray, i_arr: np.ndarray, threshold: int) -> dict:
    """
    逐像素比较，返回：
      - diff_mask: bool array, True=有差异
      - categories: 
          'design_only'  — 设计稿有内容、实装透明/空
          'impl_only'    — 实装有内容、设计稿透明/空  
          'color_diff'   — 两边都有但颜色不同
          'alpha_diff'   — 透明度不同
    """
    # 提取通道
    d_rgb = d_arr[:, :, :3]
    i_rgb = i_arr[:, :, :3]
    d_a   = d_arr[:, :, 3]
    a_i   = i_arr[:, :, 3]
    
    # RGB 差异（欧氏距离）
    rgb_dist = np.sqrt(np.sum((d_rgb - i_rgb) ** 2, axis=2))
    color_diff_mask = rgb_dist > threshold
    
    # Alpha 差异
    alpha_diff_mask = np.abs(d_a.astype(int) - a_i.astype(int)) > threshold
    
    # 内容存在性判断（alpha > 10 视为有内容）
    d_has_content = d_a > 10
    i_has_content = a_i > 10
    
    design_only = d_has_content & ~i_has_content       # 仅设计稿有
    impl_only   = i_has_content & ~d_has_content       # 仅实装有
    both_present = d_has_content & i_has_content        # 两边都有
    
    # 综合差异掩码
    combined_diff = (
        design_only |
        impl_only |
        (both_present & color_diff_mask) |
        alpha_diff_mask
    )
    
    # 分类
    categories = {
        "design_only": design_only & combined_diff,
        "impl_only":   impl_only & combined_diff,
        "color_diff":  (both_present & color_diff_mask) & combined_diff,
        "alpha_diff":  alpha_diff_mask & combined_diff,
    }
    
    total_pixels = d_arr.shape[0] * d_arr.shape[1]
    diff_pixels  = int(np.sum(combined_diff))
    
    stats = {
        "total_pixels": int(total_pixels),
        "diff_pixels": diff_pixels,
        "diff_ratio": round(diff_pixels / total_pixels * 100, 3),
        "mean_rgb_error": round(float(np.mean(rgb_dist[combined_diff])), 2) if diff_pixels > 0 else 0,
        "max_rgb_error": round(float(np.max(rgb_dist)), 2),
        "category_counts": {k: int(np.sum(v)) for k, v in categories.items()},
    }
    
    return combined_diff, categories, stats, rgb_dist


def find_connected_regions(diff_mask: np.ndarray, min_area: int = MIN_REGION_AREA) -> list:
    """
    用简化的连通域分析找到差异区块。
    返回 bbox 列表: [(x1,y1,x2,y2, area), ...]
    """
    from scipy import ndimage
    
    labeled, num_features = ndimage.label(diff_mask)
    regions = []
    
    for i in range(1, num_features + 1):
        region_mask = (labeled == i)
        area = int(np.sum(region_mask))
        if area < min_area:
            continue
        
        coords = np.where(region_mask)
        y_min, y_max = int(coords[0].min()), int(coords[0].max())
        x_min, x_max = int(coords[1].min()), int(coords[1].max())
        
        regions.append({
            "bbox": [x_min, y_min, x_max, y_max],
            "area": area,
            "center": ((x_min + x_max) // 2, (y_min + y_max) // 2),
            "aspect_ratio": round((x_max - x_min) / max(y_max - y_min, 1), 2),
        })
    
    # 按面积降序
    regions.sort(key=lambda r: r["area"], reverse=True)
    return regions


def classify_region_by_position(bbox: list, img_w: int, img_h: int) -> str:
    """根据位置给差异区域打标签"""
    x1, y1, x2, y2 = bbox
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    rw, rh = x2 - x1, y2 - y1
    
    # 横向分区
    if cx < img_w * 0.21:  # 左侧边栏区 (~220/1060)
        zone = "sidebar"
    elif cy < img_h * 0.08:  # 顶部标题栏 (~60/760)
        zone = "titlebar"
    elif cy < img_h * 0.15:  # Header 区
        zone = "header"
    elif cy < img_h * 0.23:  # Config Row
        zone = "config_row"
    elif cy < img_h * 0.32:  # Action Bar
        zone = "action_bar"
    elif cy < img_h * 0.39:  # Toolbar
        zone = "toolbar"
    elif cy < img_h * 0.45:  # Cookie Banner
        zone = "cookie_banner"
    elif cy < img_h * 0.50:  # Cookie Status
        zone = "cookie_status"
    elif cy > img_h * 0.94:  # 底部状态栏
        zone = "status_bar"
    else:
        zone = "content_area"
    
    # 形态分类
    if rh < 20 and rw > 200:
        shape = "thin_line"
    elif rw > img_w * 0.5 and rh < img_h * 0.08:
        shape = "wide_band"
    elif rw > 50 and rh > 50:
        shape = "block"
    else:
        shape = "fragment"
    
    return f"{zone}:{shape}"


def generate_overlay(
    d_arr: np.ndarray, i_arr: np.ndarray,
    diff_mask: np.ndarray, categories: dict,
    regions: list, size: tuple
) -> Image.Image:
    """
    生成差异叠加图：
      - 半透明设计稿为底
      - 红色高亮: design_only
      - 蓝色高亮: impl_only
      - 黄色高亮: color_diff
      - 绿色高亮: alpha_diff
      - 白色边框标注每个差异区域的 bbox + 序号
    """
    w, h = size
    overlay = Image.fromarray(d_arr.astype(np.uint8).copy())
    draw = ImageDraw.Draw(overlay, "RGBA")
    
    # 分类颜色映射
    colors = {
        "design_only": (255, 80, 80, 120),   # 红
        "impl_only":   (80, 120, 255, 120),   # 蓝
        "color_diff":  (255, 220, 60, 120),   # 黄
        "alpha_diff":  (80, 255, 120, 120),   # 绿
    }
    
    # 填充差异区域颜色
    for cat_name, mask in categories.items():
        color = colors.get(cat_name)
        pixels = np.where(mask)
        for idx in range(0, len(pixels[0]), 1000):  # 批量绘制避免太慢
            batch_y = pixels[0][idx:idx+1000]
            batch_x = pixels[1][idx:idx+1000]
            pts = list(zip(batch_x, batch_y))
            if len(pts) <= 2:
                continue
            draw.point(pts, fill=color)
    
    # 绘制区域 bbox 边框 + 编号
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 14)
    except OSError:
        font = ImageFont.load_default()
    
    for idx, region in enumerate(regions):
        x1, y1, x2, y2 = region["bbox"]
        label = classify_region_by_position(region["bbox"], w, h)
        
        # 边框
        draw.rectangle([x1, y1, x2, y2], outline=(255, 255, 255, 220), width=2)
        
        # 标签背景 + 文字
        text = f"[{idx}] {label} ({region['area']}px)"
        tw = font.getlength(text) if hasattr(font, 'getlength') else len(text) * 8
        draw.rectangle([x1, y1 - 22, x1 + int(tw) + 6, y1 - 2], fill=(40, 40, 40, 220))
        draw.text((x1 + 3, y1 - 20), text, fill=(255, 255, 255, 230), font=font)
    
    return overlay


def compute_ssim(d_arr: np.ndarray, i_arr: np.ndarray, mask: np.ndarray = None) -> float:
    """SSIM 计算；支持 mask 只计算指定区域（排除区填中性色）"""
    from scipy.ndimage import uniform_filter
    
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2
    
    d_gray = np.mean(d_arr[:, :, :3], axis=2)
    i_gray = np.mean(i_arr[:, :, :3], axis=2)
    
    if mask is not None:
        # 排除区域填入两边均值作为中性色，使该区域不贡献差异
        core_mean = (d_gray[mask].mean() + i_gray[mask].mean()) / 2
        d_gray = d_gray.copy()
        i_gray = i_gray.copy()
        d_gray[~mask] = core_mean
        i_gray[~mask] = core_mean
    
    d_mu = uniform_filter(d_gray, size=11)
    i_mu = uniform_filter(i_gray, size=11)
    
    d_sq = uniform_filter(d_gray ** 2, size=11)
    i_sq = uniform_filter(i_gray ** 2, size=11)
    di_mu = uniform_filter(d_gray * i_gray, size=11)
    
    d_var = d_sq - d_mu ** 2
    i_var = i_sq - i_mu ** 2
    covar = di_mu - d_mu * i_mu
    
    ssim_map = ((2 * d_mu * i_mu + C1) * (2 * covar + C2)) / \
               ((d_mu ** 2 + i_mu ** 2 + C1) * (d_var + i_var + C2))
    
    return round(float(np.mean(ssim_map)), 4)


def main():
    print("=" * 60)
    print("  像素级对比引擎 v1.0")
    print("=" * 60)
    
    # 1. 加载图片
    print(f"\n[1/5] 加载图片...")
    print(f"  设计稿: {DESIGN_PATH}")
    print(f"  实装图: {IMPL_PATH}")
    
    if not DESIGN_PATH.exists():
        # 尝试用 Pencil 导出的设计稿截图
        alt = Path(__file__).parent / "_design_screenshot.png"
        if alt.exists():
            design_src = alt
        else:
            print("  ❌ 设计稿截图不存在！请先用 Pencil get_screenshot 导出。")
            sys.exit(1)
    else:
        design_src = DESIGN_PATH

    d_arr, i_arr, size = load_images(str(design_src), str(IMPL_PATH))
    print(f"  尺寸: {size[0]}×{size[1]}")
    
    # 2. 像素级差异计算
    print(f"\n[2/5] 计算像素差异 (阈值={COLOR_THRESHOLD})...")
    diff_mask, categories, stats, rgb_dist = compute_pixel_diff(d_arr, i_arr, COLOR_THRESHOLD)
    
    # 2.5 应用排除区域（动态内容/条件显隐）
    excluded_mask = diff_mask.copy()
    excluded_mask = apply_exclude_mask(excluded_mask, size)
    excluded_pixels = int(np.sum(diff_mask & ~excluded_mask))
    print(f"  总差异像素: {stats['diff_pixels']:,} ({stats['diff_ratio']}%)")
    print(f"  排除动态区: {excluded_pixels:,}px ({excluded_pixels/stats['total_pixels']*100:.2f}%)")
    
    # 使用排除后的掩码作为"核心差异"
    diff_mask = excluded_mask
    stats['diff_pixels'] = int(np.sum(diff_mask))
    stats['diff_ratio'] = round(stats['diff_pixels'] / stats['total_pixels'] * 100, 3)
    stats['excluded_pixels'] = excluded_pixels
    print(f"  核心差异:   {stats['diff_pixels']:,} ({stats['diff_ratio']}%)  ← 仅静态UI组件")
    print(f"  平均误差:   {stats['mean_rgb_error']} (RGB)")
    print(f"  最大误差:   {stats['max_rgb_error']} (RGB)")
    print(f"  分类统计:")
    for k, v in stats['category_counts'].items():
        pct = v / stats['total_pixels'] * 100
        bar = "█" * int(pct * 2)
        print(f"    {k:14s}: {v:>7,} ({pct:>5.2f}%) {bar}")
    
    # 3. SSIM（全图 + 核心区域）
    print(f"\n[3/5] 计算 SSIM...")
    ssim_full = compute_ssim(d_arr, i_arr, mask=None)
    ssim_core = compute_ssim(d_arr, i_arr, mask=diff_mask)
    stats["ssim_full"] = ssim_full
    stats["ssim_core"] = ssim_core
    print(f"  全图 SSIM:  {ssim_full} ({'优秀' if ssim_full>0.95 else '良好' if ssim_full>0.85 else '需改进' if ssim_full>0.7 else '差'})")
    print(f"  核心 SSIM:  {ssim_core} ({'优秀' if ssim_core>0.95 else '良好' if ssim_core>0.85 else '需改进' if ssim_core>0.7 else '差'})  ← 仅静态UI组件")
    
    # 4. 连通域分析
    print(f"\n[4/5] 分析差异区域 (最小面积≥{MIN_REGION_AREA}px²)...")
    regions = find_connected_regions(diff_mask, MIN_REGION_AREA)
    print(f"  发现 {len(regions)} 个差异区域:")
    
    classified = {}
    for idx, r in enumerate(regions):
        label = classify_region_by_position(r["bbox"], size[0], size[1])
        r["label"] = label
        classified.setdefault(label, []).append(idx)
        
        x1, y1, x2, y2 = r["bbox"]
        print(f"    [{idx:>2}] {label:30s}  bbox=({x1:>4},{y1:>4},{x2:>4},{y2:>4})  "
              f"size={r['area']:>6}px²  center=({r['center'][0]:>4},{r['center'][1]:>4})")
    
    stats["num_regions"] = len(regions)
    stats["regions_by_zone"] = {k: len(v) for k, v in sorted(classified.items())}
    
    # 5. 生成叠加图
    print(f"\n[5/5] 生成差异叠加图...")
    overlay = generate_overlay(d_arr, i_arr, diff_mask, categories, regions, size)
    overlay.save(str(OUT_DIFF))
    print(f"  → {OUT_DIFF}")
    
    # 保存 JSON 报告
    report = {
        "version": "1.0",
        "images": {
            "design": str(DESIGN_PATH),
            "impl": str(IMPL_PATH),
            "size": list(size),
        },
        "settings": {
            "color_threshold": COLOR_THRESHOLD,
            "min_region_area": MIN_REGION_AREA,
        },
        "stats": stats,
        "regions": regions,
    }
    
    with open(OUT_STATS, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"  → {OUT_STATS}")
    
    with open(OUT_REGIONS, "w", encoding="utf-8") as f:
        json.dump({
            "count": len(regions),
            "classified": classified,
            "regions": [{"id": idx, **r} for idx, r in enumerate(regions)],
        }, f, ensure_ascii=False, indent=2)
    print(f"  → {OUT_REGIONS}")
    
    # 总结
    print("\n" + "=" * 60)
    print("  对比完成")
    print("=" * 60)
    grade = "A+" if ssim_core > 0.98 else "A" if ssim_core > 0.95 else \
            "B" if ssim_core > 0.90 else "C" if ssim_core > 0.80 else "D"
    print(f"  综合评级: {grade}  |  核心 SSIM={ssim_core}  |  差异率={stats['diff_ratio']}%  |  区域数={len(regions)}")
    
    if regions:
        top3 = regions[:3]
        print(f"\n  🔴 TOP 3 差异区域（优先修复）:")
        for r in top3:
            print(f"      • [{r['label']}] 面积={r['area']}px² @ {r['bbox']}")


if __name__ == "__main__":
    main()
