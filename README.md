# Taiwan ETF Dashboard

> 輕量、暗色系的 ETF 資訊 + 訂閱網站。
> FastAPI + SQLAlchemy 2.0 + SQLite + HTMX + Tailwind + ECharts。

---

## 三步啟動

### 1. 安裝套件(只做一次)

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

### 2. 設定環境變數

```bash
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux
```

不改也能跑(全部有預設值)。Telegram / 金流相關等到 Step 5+ 再填。

### 3. 啟動

```bash
python run.py
```

打開瀏覽器到:

- 首頁:http://127.0.0.1:8000
- 健康檢查:http://127.0.0.1:8000/api/health
- API 文件:http://127.0.0.1:8000/docs

第一次啟動會自動在 `data/etf.db` 建好三個 table:`etf_list` / `daily_kbar` / `news`。
console 每 60 秒會印 `[scheduler] heartbeat` 證明排程是活的。

---

## 專案結構

```
etf_dashboard/
├── app/
│   ├── main.py              # FastAPI entry + lifespan
│   ├── config.py            # pydantic-settings
│   ├── database.py          # SQLAlchemy 2.0 + Session
│   ├── scheduler.py         # APScheduler (heartbeat job)
│   ├── models/              # ETF / DailyKBar / News
│   ├── schemas/             # Pydantic schemas (待 Step 2+)
│   ├── routers/             # pages.py / api.py
│   ├── services/            # 商業邏輯 (待 Step 2+)
│   └── tg_bot/              # Telegram bot (待 Step 5)
├── templates/               # base.html / index.html
├── static/                  # CSS / JS
├── data/                    # SQLite (.gitignore)
├── tests/
├── templates_ref/           # 範本參考(寫新功能前必讀)
├── CLAUDE.md                # 開發紀律
├── requirements.txt
├── .env.example
└── run.py
```

---

## 開發路線

| Step | 內容 | 狀態 |
|------|------|------|
| 1 | 骨架(FastAPI + SQLAlchemy + 暗色首頁 + 排程) | ✅ |
| 2 | ETF 排行榜 + 每日 14:30 排程抓資料 | ⏳ |
| 3 | 績效比較圖表(ECharts) | ⏳ |
| 4 | 新聞牆 | ⏳ |
| 5 | Telegram 推播 | ⏳ |
| 6 | 會員系統 | ⏳ |
| 7 | 訂閱金流 | ⏳ |

詳細紀律與決策歷程見 `CLAUDE.md`。
