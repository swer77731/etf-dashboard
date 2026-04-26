"""ETF 分類規則 — 從代號 + 名稱判斷類別。

順序很重要(先 match 先贏),否則「ESG高股息」會被市值型搶走。
分類列表(內部代碼):
- active   主動式
- market   市值型(大盤、台灣 50、ESG 永續等)
- dividend 高股息
- overseas 海外
- theme    主題型(半導體、5G、電動車、AI...)
- leverage 槓桿反向
- bond     債券
- index    指數本身(TAIEX 大盤)— 只當對比基準
- other    其他
"""
from __future__ import annotations

import re
from typing import Callable


# (內部代碼, 中文顯示, match 函數)
_RULES: list[tuple[str, str, Callable[[str, str], bool]]] = [
    # 大盤指數本身 — 預留給 TAIEX,不靠 FinMind 的 ETF 列表
    ("index", "大盤指數",
     lambda c, n: c == "TAIEX"),

    # 槓桿反向先抓 — 名稱常含「正2 / 反1 / 槓桿 / 反向」
    ("leverage", "槓桿反向",
     lambda c, n: bool(re.search(r"正[12]|反[12]|槓桿|反向", n))),

    # 債券 ETF
    ("bond", "債券",
     lambda c, n: bool(re.search(r"債券|公債|投資級|高收益|美債|中債|公司債|金融債|可轉債", n))
                  or c.endswith("B")),

    # 主動式(2024 開放,代號以 A 結尾,或名稱含「主動」)
    ("active", "主動式",
     lambda c, n: c.endswith("A") or "主動" in n),

    # 高股息
    ("dividend", "高股息",
     lambda c, n: bool(re.search(r"高股息|高息|股息|配息|月配|季配", n))),

    # 海外
    ("overseas", "海外",
     lambda c, n: bool(re.search(
         r"S&P|SP500|那斯達克|NASDAQ|美國|日本|中國|印度|越南|新興|歐洲|韓國|港股|陸股|滬深|MSCI世界|全球|亞太", n))),

    # 主題型(產業/題材明確)
    ("theme", "主題型",
     lambda c, n: bool(re.search(
         r"半導體|5G|電動車|AI|人工智慧|電池|綠能|REIT|不動產|生技|金融|科技|機器人|元宇宙|雲端|資安|新藥", n))),

    # 市值型
    ("market", "市值型",
     lambda c, n: bool(re.search(
         r"台灣50|台50|中型100|MSCI台灣|加權|公司治理|ESG永續|領袖|等權|上市|大盤|龍頭|成長", n))),
]


CATEGORY_LABELS: dict[str, str] = {code: label for code, label, _ in _RULES}
CATEGORY_LABELS["other"] = "其他"

# 排行榜預設只開放給使用者切換的 3 大類
PUBLIC_CATEGORIES: list[tuple[str, str]] = [
    ("market",   "市值型"),
    ("dividend", "高股息"),
    ("active",   "主動式"),
]

# 白話副標(CLAUDE.md「ETF 類別白話副標」紀律 — 逐字固定不准改)
CATEGORY_DESCRIPTIONS: dict[str, str] = {
    "market":       "買到台股市值最大的公司",
    "dividend":     "主打配息,適合存股族",
    "active":       "基金經理人選股,可彈性調整持股",
    "theme":        "鎖定特定產業(半導體、AI、5G 等),電腦按指數選股",
    "bond":         "公債、公司債,波動較小",
    "leverage_pos": "短線操作工具,長期持有可能虧損",
    "leverage_neg": "短線操作工具,長期持有可能虧損",
    "top":          "不分類別,看誰最近表現最強",
}


def leverage_subtype(code: str, name: str) -> tuple[str | None, int | None]:
    """區分槓桿型 vs 反向型 + 倍數。回 (subtype, multiplier):
    - ("positive",  2)  → 正 2 倍
    - ("positive",  1)  → 正 1 倍(罕見)
    - ("inverse",  -1)  → 反 1 倍
    - ("inverse",  -2)  → 反 2 倍
    - (None, None)      → 不是槓桿/反向
    """
    code = (code or "").strip()
    name = (name or "").strip()

    m_pos = re.search(r"正\s*([12])", name)
    if m_pos:
        return "positive", int(m_pos.group(1))
    m_neg = re.search(r"反\s*([12])", name)
    if m_neg:
        return "inverse", -int(m_neg.group(1))
    if code.endswith("L"):
        return "positive", 2
    if code.endswith("R"):
        return "inverse", -1
    return None, None


def classify(code: str, name: str) -> str:
    """回傳分類內部代碼(active / market / dividend / ...),沒 match 就是 other。"""
    code = (code or "").strip()
    name = (name or "").strip()
    for cat_code, _label, fn in _RULES:
        try:
            if fn(code, name):
                return cat_code
        except Exception:
            continue
    return "other"


def label_of(category_code: str) -> str:
    return CATEGORY_LABELS.get(category_code, category_code)
