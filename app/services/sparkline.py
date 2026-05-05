"""SVG mini sparkline 產生器 — 純 SVG path,無 JS / 函式庫(紀律 #18)。

ETF 詳情頁健康度卡片用,12 個 data point。
產出可直接 inline 進 HTML,瀏覽器原生渲染,零 JS cost。
"""
from __future__ import annotations

from typing import Sequence


def render(
    values: Sequence[float],
    width: int = 120,
    height: int = 70,
    stroke: str = "#3b82f6",
    stroke_width: float = 1.5,
) -> str:
    """產 SVG sparkline string。

    Args:
        values: 數列,需 >= 2 才畫線(< 2 回空 SVG 佔位)
        width / height: viewBox 尺寸(實際渲染由 CSS width:100% 控制)
        stroke / stroke_width: 線條色與粗細
    """
    n = len(values)
    if n < 2:
        return (
            f'<svg width="100%" height="{height}" '
            f'viewBox="0 0 {width} {height}" '
            f'preserveAspectRatio="none" '
            f'xmlns="http://www.w3.org/2000/svg" '
            f'style="display:block;" aria-hidden="true">'
            f'</svg>'
        )

    vmin = min(values)
    vmax = max(values)
    span = max(vmax - vmin, 1e-9)

    pad = 2
    inner_h = height - 2 * pad
    inner_w = width - 2 * pad

    pts = []
    for i, v in enumerate(values):
        x = pad + (i / (n - 1)) * inner_w
        # SVG y 軸由上到下,翻轉:高值在上,低值在下
        y = pad + inner_h * (1 - (v - vmin) / span)
        pts.append(f"{x:.1f},{y:.1f}")

    path_d = "M " + " L ".join(pts)
    # width=100% + height=固定 + preserveAspectRatio="none":SVG 服從容器尺寸,
    # 不會自己撐到 width × ratio 撞下方表格(舊 height:auto 的 bug)。
    # Y 已在 viewBox 內 normalize,stretch 不影響閱讀。
    # vector-effect 確保線條粗細不被 stretch 壓變。
    return (
        f'<svg width="100%" height="{height}" '
        f'viewBox="0 0 {width} {height}" '
        f'preserveAspectRatio="none" '
        f'xmlns="http://www.w3.org/2000/svg" '
        f'style="display:block;" aria-hidden="true">'
        f'<path d="{path_d}" fill="none" stroke="{stroke}" '
        f'stroke-width="{stroke_width}" '
        f'vector-effect="non-scaling-stroke" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'</svg>'
    )
