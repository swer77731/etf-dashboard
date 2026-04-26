"""一次性腳本:從 FinMind 拉全部 Taiwan stock info,篩出所有 ETF,
按關鍵字粗分類,輸出 JSON 報表給人類確認。

執行:.venv\\Scripts\\python.exe scripts\\probe_etf_universe.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# 讓腳本能 import app.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from app.config import settings


OUT_DIR = Path(__file__).resolve().parent.parent / "scripts" / "out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# 分類規則(用名稱關鍵字 + 代號 pattern)
# 順序很重要,先 match 先贏(避免「高股息槓桿」歸到槓桿)
CATEGORY_RULES = [
    # 槓桿反向 — 先抓掉,免得污染其他分類
    ("槓桿反向", lambda c, n: bool(re.search(r"正[12]|反[12]|槓桿|反向", n))),

    # 債券 — 名稱有「債」幾乎是債券 ETF
    ("債券", lambda c, n: bool(re.search(r"債券|公債|投資級|高收益|美債|中債|公司債", n))),

    # 主動式 — 代號以 A 結尾(2024 法規)或名稱含「主動」
    ("主動式", lambda c, n: c.endswith("A") or "主動" in n),

    # 高股息 — 名稱含「高股息 / 高息 / 股息 / 配息」
    ("高股息", lambda c, n: bool(re.search(r"高股息|高息|股息|配息|月配|季配", n))),

    # 海外 — 含特定國家、指數或地區關鍵字
    ("海外", lambda c, n: bool(re.search(
        r"S&P|SP500|那斯達克|NASDAQ|美國|日本|中國|印度|越南|新興|歐洲|韓國|港股|陸股|滬深|MSCI世界|全球", n))),

    # 主題型 — 產業/主題明確
    ("主題型", lambda c, n: bool(re.search(
        r"半導體|5G|電動車|AI|人工智慧|電池|綠能|REIT|不動產|生技|金融|科技|機器人|元宇宙|雲端|資安|新藥", n))),

    # 市值型 — 名稱含大盤、市值、台灣 50、ESG 永續、公司治理、等權、領袖
    ("市值型", lambda c, n: bool(re.search(
        r"台灣50|台50|中型100|MSCI台灣|加權|公司治理|ESG永續|領袖|等權|上市|大盤|龍頭|成長", n))),
]


def classify(code: str, name: str) -> str:
    for label, fn in CATEGORY_RULES:
        try:
            if fn(code, name):
                return label
        except Exception:
            continue
    return "其他"


def main() -> None:
    print(f"querying FinMind TaiwanStockInfo...")
    r = httpx.get(
        "https://api.finmindtrade.com/api/v4/data",
        params={"dataset": "TaiwanStockInfo", "token": settings.finmind_api_token},
        timeout=60,
    )
    r.raise_for_status()
    payload = r.json()
    rows = payload.get("data", [])
    print(f"  total rows: {len(rows)}")

    # 找所有 ETF — industry_category 中文「ETF」或「受益憑證」
    etfs = []
    for row in rows:
        ind = (row.get("industry_category") or "").strip()
        if ind in {"ETF", "受益憑證"} or ind.startswith("ETF"):
            etfs.append({
                "code": row.get("stock_id"),
                "name": row.get("stock_name"),
                "industry": ind,
                "type": row.get("type"),
                "date": row.get("date"),
            })

    # 去重(同代號不同上市日期可能重複)
    seen = {}
    for e in etfs:
        seen.setdefault(e["code"], e)
    etfs = list(seen.values())

    print(f"  ETF count : {len(etfs)}")

    # 分類
    bucketed: dict[str, list[dict]] = {}
    for e in etfs:
        cat = classify(e["code"], e["name"])
        bucketed.setdefault(cat, []).append(e)

    # 每桶按代號排序
    for k in bucketed:
        bucketed[k].sort(key=lambda x: x["code"])

    # 輸出總覽
    summary = {cat: len(items) for cat, items in bucketed.items()}
    print("\n=== 分類統計 ===")
    for cat, n in sorted(summary.items(), key=lambda x: -x[1]):
        print(f"  {cat:8s}: {n:3d}")

    # 寫 JSON
    out_file = OUT_DIR / "etf_universe.json"
    out_file.write_text(
        json.dumps({"summary": summary, "buckets": bucketed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nfull report -> {out_file}")


if __name__ == "__main__":
    main()
