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
    stroke_width: float = 2.2,
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

    # pad: 左右 / 上下留白,避免線貼邊。8px 視覺有呼吸感不擠迫
    pad = 8
    inner_h = height - 2 * pad
    inner_w = width - 2 * pad

    pts: list[tuple[float, float]] = []
    for i, v in enumerate(values):
        x = pad + (i / (n - 1)) * inner_w
        # SVG y 軸由上到下,翻轉:高值在上,低值在下
        y = pad + inner_h * (1 - (v - vmin) / span)
        pts.append((x, y))

    # Catmull-Rom-like 平滑:用 cubic Bezier 串接,每段 control point 取相鄰
    # 數據趨勢方向。比純 L 線段順暢、不會多餘震盪、線一定通過資料點。
    parts = [f"M {pts[0][0]:.1f},{pts[0][1]:.1f}"]
    for i in range(1, n):
        p_prev = pts[i - 1]
        p_curr = pts[i]
        # cp1 從前一點往「下一段方向」探出 1/6 距離
        if i == 1:
            cp1 = p_prev
        else:
            p_pp = pts[i - 2]
            cp1 = (p_prev[0] + (p_curr[0] - p_pp[0]) / 6,
                   p_prev[1] + (p_curr[1] - p_pp[1]) / 6)
        # cp2 從當前點往「前一段反方向」拉回 1/6 距離
        if i == n - 1:
            cp2 = p_curr
        else:
            p_nn = pts[i + 1]
            cp2 = (p_curr[0] - (p_nn[0] - p_prev[0]) / 6,
                   p_curr[1] - (p_nn[1] - p_prev[1]) / 6)
        parts.append(
            f"C {cp1[0]:.1f},{cp1[1]:.1f} "
            f"{cp2[0]:.1f},{cp2[1]:.1f} "
            f"{p_curr[0]:.1f},{p_curr[1]:.1f}"
        )
    path_d = " ".join(parts)

    # width=100% + height=固定 + preserveAspectRatio="none":SVG 服從容器尺寸。
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
