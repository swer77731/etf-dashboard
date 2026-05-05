"""ETF 健康度 ctx builder — 給 ETF 詳情頁 partial 用。

組合受益人數(週)+ 規模(月)兩張卡片所需資料 + SVG sparkline。
邏輯:
- 抓最新可拿的 12 期(週 / 月)data
- 算 latest / 1m / 3m / 6m / 1y 對比百分比
- pre-format 顯示字串(萬 / 億 / 百分比帶正負號)
- 資料 < 3 期 → is_accumulating=True(前台顯示「資料累積中」)
- 完全 0 期 → 該 card = None(整段不 render)

紀律 #1 白癡都看得懂:
- 受益人數用「萬」單位(2,772,292 → 277.2 萬)
- 規模用「億」(1,316,734,573 千元 → 13,167.3 億)
- 百分比規則同 /compare 走勢圖:+45.2% / -19.5% / 0.0%
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import desc, select

from app.database import session_scope
from app.models.etf_aum import EtfAum
from app.models.etf_beneficial_count import EtfBeneficialCount
from app.services import sparkline

# === 顯示格式 helpers ===

def _fmt_holders(v: int) -> str:
    """277.2 萬"""
    if v < 10_000:
        return f"{v:,} 人"
    return f"{v / 1e4:.1f} 萬"


def _fmt_aum_thousand(v: int) -> str:
    """1,316,734,573 千元 → 13,167.3 億 / 1.32 兆"""
    yi = v / 1e5  # 千元 / 1e5 = 億
    if yi >= 10_000:
        return f"{yi / 10_000:.2f} 兆"
    return f"{yi:,.1f} 億"


def _fmt_pct_signed(v: float | None) -> str:
    """規格(同 /compare):+45.2% / -19.5% / 0.0%。None → '—'。"""
    if v is None:
        return "—"
    abs_x10 = abs(v) * 10
    sign = -1 if v < 0 else 1
    rounded = round(abs_x10) / 10 * sign
    if rounded > 0:
        return f"+{rounded:.1f}%"
    if rounded < 0:
        return f"{rounded:.1f}%"
    return "0.0%"


def _pct_class(v: float | None) -> str:
    """+ → up(紅,台股漲);- → down(綠,台股跌);0 / None → flat(灰)。"""
    if v is None:
        return "health-pct-flat"
    abs_x10 = abs(v) * 10
    if round(abs_x10) == 0:
        return "health-pct-flat"
    return "health-pct-up" if v > 0 else "health-pct-down"


def _arrow_char(v: float | None) -> str:
    """箭頭符號:▲ / ▼ / —(0 或 None)"""
    if v is None:
        return "—"
    abs_x10 = abs(v) * 10
    if round(abs_x10) == 0:
        return "—"
    return "▲" if v > 0 else "▼"


def _compute_pct(latest: float, baseline: float) -> float | None:
    if not baseline:
        return None
    return (latest - baseline) / baseline * 100


# === 資料抓取 ===

def _fetch_holders(etf_code: str) -> list[tuple[date, int]]:
    """回最近 ~52 週的 (week_date, count) 升冪。"""
    with session_scope() as s:
        rows = s.execute(
            select(EtfBeneficialCount.week_date, EtfBeneficialCount.count)
            .where(EtfBeneficialCount.etf_code == etf_code)
            .order_by(desc(EtfBeneficialCount.week_date))
            .limit(60)  # 留 buffer 給 1y 對比點(52 週)
        ).all()
    return sorted([(r.week_date, r.count) for r in rows])


def _fetch_aum(etf_code: str) -> list[tuple[date, int]]:
    """回最近 ~14 月的 (month_date, aum_thousand_ntd) 升冪。"""
    with session_scope() as s:
        rows = s.execute(
            select(EtfAum.month_date, EtfAum.aum_thousand_ntd)
            .where(EtfAum.etf_code == etf_code)
            .order_by(desc(EtfAum.month_date))
            .limit(14)
        ).all()
    return sorted([(r.month_date, r.aum_thousand_ntd) for r in rows])


# === ctx builder ===

def _build_card(
    series: list[tuple[date, int]],
    kind: str,
    title: str,
    emoji: str,
    color: str,
    fmt_value,
    period_offsets: dict[str, int],
) -> dict | None:
    """單一卡片 ctx。series 升冪 [(date, value), ...]。

    period_offsets: {"1m": 4, "3m": 12, ...} — 期數位移(週 → 4/12/26/52)
    """
    if not series:
        return None

    n = len(series)
    latest_date, latest_v = series[-1]

    # 主數值「上下箭頭」對比上一期
    arrow_pct = None
    if n >= 2:
        prev = series[-2][1]
        arrow_pct = _compute_pct(latest_v, prev)

    # 表格 5 列:最新 + 4 期對比
    rows = [{
        "label": "最新",
        "value_display": fmt_value(latest_v),
        "pct_display": "—",
        "pct_class": "health-pct-flat",
    }]
    for label, off in period_offsets.items():
        idx = n - 1 - off
        if idx < 0:
            rows.append({
                "label": label,
                "value_display": "—",
                "pct_display": "資料累積中",
                "pct_class": "health-pct-flat",
            })
        else:
            v = series[idx][1]
            pct = _compute_pct(latest_v, v)
            rows.append({
                "label": label,
                "value_display": fmt_value(v),
                "pct_display": _fmt_pct_signed(pct),
                "pct_class": _pct_class(pct),
            })

    # Sparkline:取最後 12 點
    spark_values = [s[1] for s in series[-12:]]
    spark_svg = sparkline.render(spark_values, stroke=color)

    return {
        "kind": kind,
        "title": title,
        "emoji": emoji,
        "latest_display": fmt_value(latest_v),
        "arrow_pct_display": _fmt_pct_signed(arrow_pct),
        "arrow_char": _arrow_char(arrow_pct),
        "arrow_class": _pct_class(arrow_pct),
        "spark_svg": spark_svg,
        "rows": rows,
        "data_count": n,
        "is_accumulating": n < 3,
    }


def build_ctx(etf_code: str) -> dict:
    """ETF 詳情頁健康度區塊 ctx — 受益人數 + 規模兩張卡片。"""
    holders = _fetch_holders(etf_code)
    aum = _fetch_aum(etf_code)

    holders_card = _build_card(
        series=holders,
        kind="holders",
        title="受益人數",
        emoji="👥",
        color="#3b82f6",  # 藍
        fmt_value=_fmt_holders,
        period_offsets={
            "1 個月前": 4,    # 4 週 ≈ 1 月
            "3 個月前": 12,   # 12 週 ≈ 3 月
            "6 個月前": 26,
            "1 年前": 52,
        },
    )
    aum_card = _build_card(
        series=aum,
        kind="aum",
        title="規模",
        emoji="💰",
        color="#f59e0b",  # 橘
        fmt_value=_fmt_aum_thousand,
        period_offsets={
            "1 個月前": 1,
            "3 個月前": 3,
            "6 個月前": 6,
            "1 年前": 12,
        },
    )

    return {
        "has_health_data": bool(holders_card or aum_card),
        "holders_card": holders_card,
        "aum_card": aum_card,
    }
