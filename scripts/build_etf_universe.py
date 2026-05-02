"""一次性 — 用 FinMind 股東人數資料,排序輸出 Top 80 熱門 ETF 清單。

目的:
    為 DCA 試算器產生「真實熱度」白名單,取代現有 14 支精選清單。
    本腳本只負責「產生清單 + 印出來給 user 審」,不動 etf_yearly_returns、
    不動 dca.html 白名單、不 commit。等 user 確認清單後另起 backfill 任務。

執行:
    python scripts/build_etf_universe.py
    或:.venv/Scripts/python scripts/build_etf_universe.py

預估執行時間:5~10 分鐘(主要是 FinMind throttle 1s/call)。
"""
from __future__ import annotations

import csv
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# === 篩選參數(spec 定義)===
DELIST_GRACE_DAYS = 90        # date 欄位早於 N 天前 → 視為下市
MIN_HOLDERS = 2000            # 股東人數 < N → 太冷門過濾(< 2000 散戶幾乎沒在買)
TOP_N = 80
HOLDERS_LOOKBACK_DAYS = 30    # 股東表抓最近 30 天取最新


# === 類別判斷規則(優先序由上而下)===
def classify_etf(stock_id: str, name: str) -> str:
    """依代號字尾 + 名稱關鍵字判斷類別。回傳 type 內部 key。

    優先序:
      1. 代號結尾 R → 反向(已在 is_reverse_etf 排除,不會走到這)
      2. 代號結尾 A → 主動(法規層級分類,優先於 dividend keyword)
      3. 代號結尾 U → 商品(期信信託,實質商品/期貨型)
      4. 代號結尾 L → 槓桿
      5. 名稱含 高息 / 高股息 / 優息 / 月配 / 股利 → 高息
      6. 名稱含 債 / 美債 / 公債 / 投等債 / 非投等債 → 債券
      7. 名稱含 不動產 / REIT → REIT
      8. 其餘 → 股票型 equity
    """
    sid = stock_id or ""
    # 1-4: 代號字尾(法規層級分類最可靠)
    if len(sid) >= 5:
        suffix = sid[-1]
        if suffix == "A":
            return "active"
        if suffix == "U":
            return "commodity"
        if suffix == "L":
            return "leverage"
    # 5: 高息類(優息 / 月配 / 股利 都歸高息)
    if any(kw in name for kw in ("高股息", "高息", "優息", "月配", "股利")):
        return "dividend"
    # 6: 債券
    if any(kw in name for kw in ("債", "美債", "公債", "投等債", "非投等債")):
        return "bond"
    # 7: REIT
    if any(kw in name for kw in ("不動產", "REIT")):
        return "reit"
    # 8: 預設股票型
    return "equity"


TYPE_LABEL_ZH = {
    "equity": "股票型",
    "dividend": "高息",
    "active": "主動",
    "leverage": "槓桿",
    "commodity": "商品",
    "bond": "債券",
    "reit": "REIT",
}


# === 反向 / 反 ETF 篩除(spec 規則)===
_REVERSE_RE = re.compile(r"反\s?[1一]|-R\b|反向")


def is_reverse_etf(stock_id: str, name: str) -> bool:
    """名稱含「反 1 / 反一 / -R / 反向」或代號結尾 R → 反向 ETF。"""
    if _REVERSE_RE.search(name or ""):
        return True
    # 代號結尾 R(00632R 等)
    if stock_id and stock_id.endswith("R") and len(stock_id) >= 5:
        return True
    return False


def fetch_etf_universe() -> list[dict]:
    """從 FinMind TaiwanStockInfo 撈所有 industry_category=ETF。"""
    from app.services import finmind
    data = finmind.request("TaiwanStockInfo")
    etfs = [r for r in data if r.get("industry_category") == "ETF"]
    return etfs


def fetch_total_holders(stock_id: str) -> tuple[int, str | None]:
    """抓單支 ETF 最新股東人數。回傳 (total_holders, latest_date) 或 (0, None)。

    注意:HoldingSharesLevel 'total' row 已是合計,不要再 sum 全部 row(會 2x)。
    """
    from app.services import finmind
    today = datetime.now().date()
    start = (today - timedelta(days=HOLDERS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    try:
        rows = finmind.request(
            "TaiwanStockHoldingSharesPer",
            data_id=stock_id,
            start_date=start,
            end_date=end,
        )
    except Exception as e:
        print(f"  [WARN] {stock_id} holders fetch error: {e}", file=sys.stderr)
        return 0, None
    if not rows:
        return 0, None
    # 取最新日期
    latest_date = max(r["date"] for r in rows)
    latest_rows = [r for r in rows if r["date"] == latest_date]
    # 'total' row 直接拿
    total_row = next(
        (r for r in latest_rows if r.get("HoldingSharesLevel") == "total"),
        None,
    )
    if total_row:
        return int(total_row["people"]), latest_date
    # fallback: sum all levels excluding 差異數調整
    holders = sum(
        int(r["people"]) for r in latest_rows
        if r.get("HoldingSharesLevel") not in ("total",)
        and "差異" not in str(r.get("HoldingSharesLevel", ""))
    )
    return holders, latest_date


def fetch_listing_year(stock_id: str) -> int | None:
    """從 TaiwanStockPrice 取最早交易日的年份。失敗回 None。"""
    from app.services import finmind
    try:
        rows = finmind.request(
            "TaiwanStockPrice",
            data_id=stock_id,
            start_date="1990-01-01",
            end_date=datetime.now().strftime("%Y-%m-%d"),
        )
    except Exception as e:
        print(f"  [WARN] {stock_id} price fetch error: {e}", file=sys.stderr)
        return None
    if not rows:
        return None
    earliest = min(r["date"] for r in rows)
    return int(earliest[:4])


def main() -> None:
    print("=" * 70)
    print("ETF Universe Top 80 — FinMind 股東人數排序")
    print("=" * 70)

    # 配額預檢
    from app.services import finmind
    q = finmind.log_quota("[before-start] ")
    print(f"[quota] used={q.used}/{q.limit_hour} ({q.ratio:.1%})  room={q.room}  level={q.level}")
    print()

    # === 1. 撈 ETF 清單 ===
    print("[1/4] Fetch TaiwanStockInfo (ETF universe)...")
    raw = fetch_etf_universe()
    n_total = len(raw)
    print(f"      raw ETF count: {n_total}")

    # 過濾下市 + 反向 + 排除非 ETF(stock_id 至少 4 碼數字開頭)
    today = datetime.now().date()
    cutoff = today - timedelta(days=DELIST_GRACE_DAYS)
    cutoff_str = cutoff.strftime("%Y-%m-%d")

    filtered = []
    n_delisted = 0
    n_reverse = 0
    n_invalid = 0
    for r in raw:
        sid = r.get("stock_id", "")
        name = r.get("stock_name", "")
        date_str = r.get("date", "")
        if not sid or not name:
            n_invalid += 1
            continue
        if date_str and date_str < cutoff_str:
            n_delisted += 1
            continue
        if is_reverse_etf(sid, name):
            n_reverse += 1
            continue
        filtered.append(r)

    print(f"      excluded delisted (date < {cutoff_str}): {n_delisted}")
    print(f"      excluded reverse ETFs: {n_reverse}")
    print(f"      excluded invalid: {n_invalid}")
    print(f"      surviving: {len(filtered)}")
    print()

    # === 2. 抓股東人數 ===
    print(f"[2/4] Fetch TaiwanStockHoldingSharesPer for {len(filtered)} ETFs...")
    print("      (FinMind throttle 1s/call,預估 ~%.1f 分鐘)" % (len(filtered) * 1.05 / 60))
    enriched: list[dict] = []
    t0 = time.time()
    for i, r in enumerate(filtered, 1):
        sid = r["stock_id"]
        name = r["stock_name"]
        holders, latest_date = fetch_total_holders(sid)
        etype = classify_etf(sid, name)
        enriched.append({
            "stock_id": sid,
            "stock_name": name,
            "total_holders": holders,
            "etf_type": etype,
            "latest_date": latest_date,
        })
        if i % 20 == 0 or i == len(filtered):
            elapsed = time.time() - t0
            print(f"      [{i:>3}/{len(filtered)}] {sid:<8} {name[:20]:<25}  "
                  f"holders={holders:>10,}  ({elapsed:.0f}s elapsed)")

    # === 3. 過濾與排序 ===
    print()
    print("[3/4] Filter + sort...")
    n_no_data = sum(1 for e in enriched if e["total_holders"] == 0)
    n_too_cold = sum(1 for e in enriched if 0 < e["total_holders"] < MIN_HOLDERS)
    qualified = [e for e in enriched if e["total_holders"] >= MIN_HOLDERS]
    qualified.sort(key=lambda x: x["total_holders"], reverse=True)
    print(f"      no holders data: {n_no_data}")
    print(f"      too cold (< {MIN_HOLDERS:,}): {n_too_cold}")
    print(f"      qualified: {len(qualified)}")

    top = qualified[:TOP_N]
    print(f"      top {TOP_N}: {len(top)}")

    # === 4. 補上市年份(只跑 Top 80,省 quota)===
    print()
    print(f"[4/4] Fetch listing year for top {len(top)} (TaiwanStockPrice min date)...")
    t0 = time.time()
    for i, e in enumerate(top, 1):
        sid = e["stock_id"]
        year = fetch_listing_year(sid)
        e["listing_year"] = year
        if i % 20 == 0 or i == len(top):
            elapsed = time.time() - t0
            print(f"      [{i:>3}/{len(top)}] {sid:<8} year={year}  ({elapsed:.0f}s elapsed)")

    # === 5. 輸出 markdown 表格 ===
    print()
    print("=" * 70)
    print(f"TOP {TOP_N} 熱門 ETF(按股東人數排序)")
    print("=" * 70)
    print()
    print("| Rank | 代號 | 名稱 | 股東人數 | 類別 | 上市年份 |")
    print("|------|------|------|---------:|------|---------:|")
    for i, e in enumerate(top, 1):
        print(f"| {i} | {e['stock_id']} | {e['stock_name']} | "
              f"{e['total_holders']:,} | {TYPE_LABEL_ZH.get(e['etf_type'], e['etf_type'])} | "
              f"{e.get('listing_year') or '-'} |")

    # === 6. 存 CSV ===
    print()
    csv_path = ROOT / "etf_universe" / "top80.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "stock_id", "stock_name", "total_holders",
                    "etf_type", "listing_year"])
        for i, e in enumerate(top, 1):
            w.writerow([i, e["stock_id"], e["stock_name"],
                        e["total_holders"], e["etf_type"],
                        e.get("listing_year") or ""])
    print(f"CSV saved: {csv_path}")

    # === 7. 統計摘要 ===
    print()
    print("=== 統計摘要 ===")
    by_type: dict[str, int] = defaultdict(int)
    for e in top:
        by_type[e["etf_type"]] += 1
    total_holders_top = sum(e["total_holders"] for e in top)
    print(f"raw ETF (TaiwanStockInfo):     {n_total}")
    print(f"after exclusion (active+long): {len(filtered)}")
    print(f"qualified (>= {MIN_HOLDERS:,}):     {len(qualified)}")
    print(f"top {TOP_N} captured holders:       {total_holders_top:,}")
    if len(top) >= TOP_N:
        print(f"#{TOP_N} threshold:                  {top[-1]['total_holders']:,} ({top[-1]['stock_id']})")
    print()
    print("Top 80 by type:")
    for t, label in TYPE_LABEL_ZH.items():
        n = by_type.get(t, 0)
        if n:
            print(f"  {label:<8}: {n}")

    # 配額後檢
    print()
    q2 = finmind.log_quota("[after-finish] ")
    print(f"[quota] used={q2.used}/{q2.limit_hour} ({q2.ratio:.1%})  "
          f"this run consumed ~{q2.used - q.used} calls")


if __name__ == "__main__":
    main()
