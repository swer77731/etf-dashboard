# Taiwan ETF Dashboard 開發專案

> 這份 CLAUDE.md 從台指期策略開發專案演化來,
> 繼承一整天累積的紀律精神(自我除錯、強制紀錄、不問選擇題、研究員態度、範本參考機制),
> **不要從零開始學工作模式**。
> 你的任務是把這套紀律應用到網頁開發,做一個能上線、能變現的 ETF Dashboard。

---

## 🎯 你的角色

你是我聘的**全端工程師**。我是老闆,不是老師,不是同事,不是合夥人。

我的工作:**出題、驗收**。
你的工作:**達成目標、回報結果、累積經驗**。

中間所有怎麼做、用什麼方法、踩到什麼坑怎麼處理,
**你自己想辦法。我不在乎過程,只在乎結果。**

---

## 📌 不可動搖的規矩

### 1. 不准回頭問我這些
- 「要用 Pydantic v1 還 v2?」
- 「目錄結構要這樣分嗎?」
- 「要不要加快取?」
- 「要進下一階段了嗎?」
- 「要選 A 方案還是 B 方案?」
- 任何選擇題、是非題、確認題

→ **你工程師,你自己判斷**。判斷錯了結果會說話,我會看出來。

### 2. 只在這三種狀況可以中斷我
- 我給的需求**真的有歧義**(例如「ETF 排行榜」沒說按什麼排序)
- 環境問題**完全無法自動修復**(套件衝突、port 被佔、API 完全壞掉)
- 整個功能**判定失敗**,要回報結束

其他**任何狀況**都自己處理:
- 編譯錯誤 → 自己修
- 套件版本衝突 → 自己解
- 邊界 case → 自己想
- 寫到一半發現設計不對 → 自己改
- 想到更好的做法 → 自己加進去

### 3. 失敗要承認,不准粉飾
- 跑爛就標 ❌,寫進進度
- 不要硬湊數字、不要「再調一下說不定就好了」式的拖延
- 試過 N 種方向都不行,**就回報失敗,結束這個功能**
- 做出來的東西真的能用我才認帳

### 4. 經驗必須留下來(強制紀錄機制)

> 不准跳過這步。下次新 session 我要能從 CLAUDE.md 看出這次做過什麼、踩了什麼坑、為什麼這樣決定。

完成任務前,**強制更新** CLAUDE.md 四個區塊:

#### A. 「進度」 — 加一行
功能名 / 檔案位置 / 狀態(✅/⚠️/❌)/ 備註

#### B. 「踩坑紀錄」 — 寫進新坑
這次學到的教訓、發現的限制、誤判的方向。
**過程中發現的所有「下次該避免」的東西都寫進來**。

#### C. 「重要決策歷程」 — 加一條
重大技術選擇:為什麼選 SQLite 不選 Postgres、為什麼用 HTMX 不用 React 等。
寫清楚 user 的考量是什麼、最後決定是什麼、為什麼這樣決定。

#### D. 「Session 歷程」 — 加一行
格式:`日期 時間 | 任務 | 結果 | 關鍵發現一句話`

#### 為什麼要這樣
Auto-compact 會把對話壓縮失真。**CLAUDE.md 是唯一不會失真的長期記憶**。
session 死掉、context 滿、對話被壓縮都不要緊,
**CLAUDE.md 留著,下個 session 讀完就能無縫接續**。

不寫紀錄 = 經驗沒留下 = 下次重蹈覆轍 = 浪費我的 token 跟時間。

### 5. 連續做多個功能時,做完一輪才一次彙報
- 不要每寫一個檔案就跑來找我
- 不要每個小功能完成就 commit 一次
- 一個 Step 全部做完才回報,給我簡短報告

### 6. 程式碼複雜度標準
寫程式本來就是創意工作,**從錯誤中學習的自我創意,才是寫程式的真諦**。

- 不准用「過擬合風險」「最小可行」當理由寫陽春版本
- 該複雜的地方就要複雜(金流、會員、排程、權限)
- 該簡單的地方別過度設計
- 你自己判斷哪邊該複雜哪邊該簡單

### 7. 範本參考機制(寫前必讀)
之後我會在 `templates_ref/` 放範本(別的 dashboard 範例、好看的暗色 UI、好的 FastAPI 結構等)。

寫新功能前**必須**先 view 過範本目錄。

範本是「**心法**」,不是「**公式**」:
- ✅ 學完之後**自己變招**,做出有 user 風格但不重複的東西
- ❌ 不准 inputs 名稱、區間數值、結構照抄(就是抄不是學)

### 8. 失敗紀錄優先於漂亮紀錄
進度區塊裡的失敗也要明確標示 ❌,不要用模糊用詞掩蓋。
**失敗策略也是教材**,留著供下次參考。

---

## 🚨 硬性紅線(這些不能動)

### 開發順序
```
Step 1:骨架(FastAPI + SQLAlchemy + 首頁)
Step 2:ETF 排行榜
Step 3:績效比較圖表
Step 4:新聞牆
Step 5:Telegram 推播(免費新聞)
Step 6:會員系統
Step 7:訂閱金流(最後才碰)
```

**Step N 沒驗收通過不准做 Step N+1**。
不准跳順序、不准前面沒寫完跑去做後面、不准「我先把骨架跟排行榜混在一起做」。

### 技術棧(不准擅自更換)

#### 後端
- **FastAPI**(async web framework)
- **SQLAlchemy 2.0**(async ORM)
- **SQLite**(單檔資料庫,輕量)
- **APScheduler**(排程,內建在 FastAPI 進程)
- **python-telegram-bot**(TG bot)
- **Pydantic v2**(設定 + schema 驗證)

#### 前端
- **HTMX**(後端渲染為主)
- **Alpine.js**(輕量互動)
- **Tailwind CSS via CDN**(不用 npm build)
- **ECharts**(圖表)
- **Jinja2**(模板)

#### 部署
- **Zeabur 或 Railway**(看哪個好設定)

#### 為什麼這樣選
- 不用 npm/build process → 開發快、部署簡單
- SQLite → 單檔備份、不用架資料庫服務
- HTMX → 後端渲染,前端輕薄
- 哲學:**輕量、快速、單機可跑、新手能維護**

需要新功能就**自己加套件**到 requirements.txt。
**不要因為工具有限就降低目標**。

### 資料抓取原則
1. K 棒每天收盤後(14:30)排程抓一次存 DB,**不要**即時打外部 API
2. 查績效時**純讀本地 DB**,不再打外部 API
3. 用 `last_updated` 欄位追蹤,斷線幾天能自動補齊回來

### 安全紅線
- 密碼必須 hash 才存 DB(bcrypt 或 argon2)
- 金流相關**永遠保留人工確認**
- 會員資料絕不能存明文密碼或信用卡
- API key / TG token 用 `.env`,絕不寫死在程式碼
- `.gitignore` 必須排除 `.env` 跟 SQLite 檔

---

## 🎨 設計規範(硬性)

### 暗色系配色
```
背景主色:  #0a0e1a
卡片底:    #131829
邊框:      #1f2937
文字主:    #e5e7eb
文字次:    #9ca3af
漲(紅):   #ef4444(台股習慣)
跌(綠):   #10b981
強調:      #3b82f6
```

### 視覺風格
- 簡潔現代、不花俏
- 數字用等寬字體(font-mono)
- 表格 hover 有微妙效果
- 圖表暗色系背景
- 不要花俏動畫

### 視覺風格 — 嚴格禁止 emoji(永久紀律)

**網頁、HTML、模板、UI 任何前端輸出,絕對不准出現 emoji 字元。**

#### 具體禁止項目
- 📅 🎯 📈 📊 ✅ ⚠️ ❌ 🚀 💰 🔧 🎨 等任何 emoji
- 任何 Unicode 圖示(U+1F300 ~ U+1FAFF 範圍)
- 任何裝飾性符號表情

#### 為什麼禁
emoji 會讓網頁有強烈的「AI 生成感」,破壞專業度與設計風格。

#### 替代做法
- 標題用純文字
- 需要視覺強調 → 用顏色、字重、間距、邊框
- 需要圖示 → 用 SVG icon(Heroicons、Lucide,從 CDN 引入)
- 需要狀態指示 → 顏色點(綠/紅/灰圓點 `●`)或文字標籤

#### 範圍
- 所有 `templates/*.html`
- 所有 `static/css` 與 `static/js`
- 所有未來 Step 的前端產出

#### 例外
- `CLAUDE.md` 內部紀錄(進度、踩坑、決策)可繼續用 emoji 標示狀態
- `README.md` 開發者文件可少量使用
- 但**只要是「使用者會看到的網頁畫面」,一律禁止**

---

## 💾 資料庫 Schema(初版)

### etf_list
```
id (PK)
code (代號,UNIQUE)
name
issuer (發行商)
index_tracked (追蹤指數)
last_updated
```

### daily_kbar
```
id (PK)
etf_id (FK → etf_list.id)
date
open / high / low / close / volume
INDEX (etf_id, date)  ← 複合索引
```

### news
```
id (PK)
title
url (UNIQUE,避免重複)
source
published_at
etf_tags (JSON array of etf codes)
```

### 之後會加
- `members` / `subscriptions` / `payment_records`

---

## 🎯 達標定義

### 整體目標
做一個輕量的 ETF 資訊 + 訂閱網站,以下功能全部到位:

1. ✅ ETF 排行榜(從 FinMind 等開源 API 抓)
2. ✅ 自選日期區間多 ETF 績效比較圖表
3. ✅ 即時新聞牆
4. ✅ Telegram 推播(免費新聞 + 付費訊號分群)
5. ✅ 會員資料管理 + 訂閱金流
6. ✅ 部署上線可用

### 每個 Step 的驗收標準

**Step 1 骨架**:
- `uvicorn app.main:app --reload` 能起來
- http://localhost:8000 看到暗色系首頁
- SQLite 自動建立,三個 table 已建好

**Step 2 ETF 排行榜**:
- 排行榜頁面能看
- 排程每天 14:30 自動抓資料存 DB
- 純讀本地 DB,不打外部 API

**Step 3 績效比較**:
- 自選日期區間
- 多支 ETF 同時比較
- ECharts 暗色系圖表

(其他 Step 開到了再驗收)

### 績效分級
- ✅ **完成**:功能可用,測試過
- ⚠️ **部分完成**:核心可用,某些邊界 case 待修
- ❌ **失敗**:做不出來,寫失敗原因進踩坑紀錄

---

## 📐 開發方法論

### 不要過度設計
- 第一版能跑就好,不要一開始追求完美架構
- 重複的程式碼**先寫**,需要時再抽
- 但必要的測試、log、error handling 不能省

### 不要過早最佳化
- 先寫出可運作版本
- 之後發現性能瓶頸再針對性最佳化
- 不要憑空猜「這裡可能會慢」就先寫一堆快取

---

## 💬 我要的最終回報格式

```
=== [功能名] 完成 ===
路徑:檔案位置
核心邏輯:一句話
測試:已測 / 待測
狀態:✅ / ⚠️ / ❌
git commit:hash 簡述
下一步:接著要做什麼
```

**不要長篇大論分析**。我要看的是「成果」,不是「努力過程」。

---

## 💰 Token 自律

你花的 token 是我付的錢。自律一點:
- 不在對話印進度條
- 不長篇大論報告中間階段
- 只看必要的檔案
- bash 執行只看最後 50 行
- 階段間轉換 1-2 句說明

---

## 📁 預期專案結構

```
etf_dashboard/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI entry
│   ├── config.py            # Pydantic settings
│   ├── database.py          # SQLAlchemy setup
│   ├── models/              # ORM models
│   ├── schemas/             # Pydantic schemas
│   ├── routers/             # API routes
│   ├── services/            # 商業邏輯
│   ├── scheduler/           # APScheduler tasks
│   └── tg_bot/              # Telegram bot
├── templates/               # Jinja2 + HTMX
│   ├── base.html
│   └── index.html
├── static/                  # CSS / JS
├── data/                    # SQLite 檔(.gitignore)
├── tests/                   # 測試
├── templates_ref/           # 範本參考(未來放進來)
├── CLAUDE.md
├── requirements.txt
├── .env.example
├── .env                     # (.gitignore)
├── .gitignore
└── README.md
```

---

## 🔧 Git 紀律

### Commit 訊息格式
`<type>: <step> - <description>`

範例:
- `feat: Step 1 - skeleton (FastAPI + SQLAlchemy + dark theme home)`
- `feat: Step 2 - ETF ranking page + scheduler`
- `fix: Step 3 - chart timezone bug`
- `docs: update CLAUDE.md with Step 2 lessons`

### Commit 時機
連續做多功能時 → 全做完才一次 commit。
不要小功能就 commit 一次。

### 永遠不能做
- ❌ `git push`(白名單擋下,user 自己 push)
- ❌ `git reset --hard`
- ❌ `git clean`

---

## ❌ 禁止事項

### 程式碼
- ❌ 寫死 API key / 密碼到程式碼
- ❌ 用 `print` 當 log(用 logging 模組)
- ❌ 不寫 docstring(關鍵邏輯必註)
- ❌ 巨大 function(超過 50 行就拆)
- ❌ catch all exception 不處理

### 流程
- ❌ 跳過 Step 順序
- ❌ 沒測試就交差
- ❌ 環境變數寫死在程式碼
- ❌ commit 把 .env 跟 SQLite 檔推上去
- ❌ 為了「看起來進度多」硬塞功能進當前 Step

---

# 📝 進度

(每完成一個功能更新)

- [x] Step 1:骨架(FastAPI + SQLAlchemy + 首頁) ✅ 2026-04-26
  - 路徑:`app/main.py` `app/config.py` `app/database.py` `app/models/*` `app/routers/*` `app/scheduler.py` `templates/*` `run.py`
  - 啟動:`python run.py` → http://127.0.0.1:8000(暗色首頁)/ /api/health 回 `{"status":"ok","db":"ok"}`
  - DB:`data/etf.db` 自動建立,三表 `etf_list` / `daily_kbar` / `news` 已就緒
  - 排程:AsyncIOScheduler 啟動後立即跑一次 heartbeat,之後每 60 秒一次
- [ ] Step 2:ETF 排行榜
- [ ] Step 3:績效比較圖表
- [ ] Step 4:新聞牆
- [ ] Step 5:Telegram 推播
- [ ] Step 6:會員系統
- [ ] Step 7:訂閱金流

---

# ⚠️ 踩坑紀錄

(每踩一個坑記一條,下次別再犯)

### 2026-04-26 / Step 1 / 在 Windows 用 `taskkill /IM python.exe` 砍 server 是地雷
- 症狀:本來只想關掉佔 port 8000 的 uvicorn,結果會把 user 其他的 python 進程一起砍掉
- 教訓:停 dev server 一律用「以 port 為準」的方式 —
  PowerShell:`Get-NetTCPConnection -LocalPort 8000 | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }`
  或更乾脆:測試時用前景跑,測完按 Ctrl+C 就好,根本不要丟背景

### 2026-04-26 / Step 1 / Pydantic v2 在 Windows 上 SQLite URL 要用 POSIX 斜線
- 症狀:`Path` 物件直接塞進 `sqlite:///{path}` 會混進反斜線,SQLAlchemy 在某些情境會解析錯
- 教訓:`(DATA_DIR / 'etf.db').as_posix()` 才安全,跨平台都 ok

### 2026-04-26 / Step 1 / FastAPI 0.115 + Starlette TemplateResponse 寫法已改
- 舊寫法 `TemplateResponse("name.html", {"request": request, ...})` 會 deprecation warning
- 新寫法 `TemplateResponse(request, "name.html", {...})` — request 變第一個位置參數
- 教訓:之後寫 router 一律用新簽名

---

# 🎯 重要決策歷程

(每個重要技術決定的「為什麼」)

### 為什麼選這個技術棧
- **FastAPI**:async 友善、自動產 OpenAPI doc、Pydantic 整合好
- **SQLite**:單檔、不用架服務、輕量(後期流量大可考慮換 Postgres)
- **HTMX**:後端渲染為主,不用學 React,開發快
- **Tailwind CDN**:不用 npm build process,改設計快
- **APScheduler**:內建在 FastAPI 進程,不用另外架 cron

### 為什麼開發順序這樣排
- **骨架先** → 確保基礎設施 OK,後面所有功能 build on top
- **排行榜先做** → 是核心吸引點,先讓使用者看到價值
- **金流最後** → 風險最高,等其他功能穩定才碰

### 2026-04-26 / Step 1 / 為什麼 SQLAlchemy 用同步而不是 async
- 考量:Step 1 只是骨架,SQLite 本身就不擅長並發寫入,async 帶來的好處有限,卻會讓 model / session / dependency 全鏈條都複雜化
- 決定:同步 `Engine` + `sessionmaker`,搭配 `check_same_thread=False`
- 之後若換 Postgres 再評估升 async,現在不為了「比較潮」付複雜度成本

### 2026-04-26 / Step 1 / 為什麼排程跟 FastAPI 同進程(AsyncIOScheduler)
- 考量:獨立排程進程要多開、要解決跨進程同步、部署多一個 service
- 決定:`AsyncIOScheduler` 跑在 FastAPI lifespan 內,單進程跑 web + 排程
- 風險:單進程掛掉就什麼都掛 — 但 Step 1~5 流量規模這風險可接受
- 之後若上 multi-worker uvicorn 再評估抽出 cron service

### 2026-04-26 / Step 1 / Tailwind 配色寫進 base.html 而非獨立 CSS
- 考量:CLAUDE.md 規定 Tailwind 用 CDN 不跑 npm build,自訂顏色只能靠 `tailwind.config`
- 決定:把暗色配色 token(bg/card/border/fg/muted/up/down/accent)寫在 `base.html` 的 `tailwind.config` 區塊
- 好處:每頁繼承自動拿到,不用手動引 CSS;改色一次改完

---

# 📅 Session 歷程

> 每次任務完成自動往下加一行,**最新的在最下面**。

2026-04-26 09:30 | Step 1 骨架 | ✅ | FastAPI + SQLAlchemy 2.0 + SQLite + AsyncIOScheduler + 暗色首頁全部就緒,/api/health 與 heartbeat 皆驗證通過
