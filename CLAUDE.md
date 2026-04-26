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

### 9. 雙重確認 + 自主除錯(2026-04-26 鎖定 by user)

> 「任何東西請一定要重複雙重確認...自己要有懂得除錯的能力,要有解決事情的能力」— user 原話

#### 9.1 雙重確認(任何輸出前都要驗)
- 任何「數字」「清單」「名稱」要呈現給 user 前,**至少用兩種方式驗證一次**
  - 例:報「主動式 ETF 有 21 支」→ 不能憑記憶,要打 FinMind API 撈真實清單(已踩過坑,寫進踩坑紀錄)
  - 例:算 0050 近 1 年報酬 → 不能只用 raw close 算一次,要同時用 adj close 算,且最終要比對 Yahoo 官網 / 元大網站
  - 例:寫 SQL query 抓資料 → 跑完要對 row count、抽樣檢查 1-2 筆
- API 回傳的欄位**不要假設**,先打一次看實際回傳結構,再寫程式
- 上線前用真實 case 對照外部來源(Yahoo / 證交所 / 元大),數字對得上才算過關

#### 9.2 自主除錯(不准遇坑就回頭問)
- 遇到錯誤 → 先自己看 log、看 traceback、看 API 回傳、看 DB 內容
- 套件版本衝突 → 自己查 changelog、自己改寫法
- 邊界 case 沒處理 → 自己想、自己加 try/except、自己加 fallback
- 寫到一半發現設計不對 → 自己改、自己重構,不要拖
- 真的卡死 → 才回報「這個我試過 A B C 三條路都不通,需要你決策方向」(具體報告,不是「不會」)

#### 9.3 解決事情的能力(主動補完缺口)
- 看到程式有 race condition / SQL injection / N+1 query → 順手修
- 發現 model 缺欄位 / route 缺驗證 / log 不夠 → 順手補
- 範本檔有 emoji 漏網 → 順手刪
- 不要「user 沒講我就不做」,該做的就主動做

#### 落地檢查清單(每次回報前自問)
- [ ] 我給的數字有沒有用 API / DB / 真實工具驗過?
- [ ] 我寫的程式碼有沒有實際 run 過,看 log 確認?
- [ ] 我宣稱「跑通了」的功能,有沒有用 curl / 瀏覽器親自打過?
- [ ] 中途遇到的小坑有沒有寫進「踩坑紀錄」?

---

## 🚨 硬性紅線(這些不能動)

### 開發順序(2026-04-26 全面更新 / 8 Steps)

```
Step 1   骨架(FastAPI + SQLAlchemy + 首頁)            ✅ 完成
Step 2   ETF 排行榜(B 公式 + 配息 + 浮水印 + 法律頁)   進行中
Step 2.5 ETF 詳情頁 /etf/{code}(基本資訊 + 走勢 + 持股 + 配息)
Step 3   績效比較圖表(自選 ETF + 自選區間 + 統計表)
Step 4   新聞牆(完全免費,鎖流量入口)
Step 5  觀察期(1~2 個月,不寫程式)
        - 加 Google Analytics 4
        - 看真實使用者行為與留存
        - user 拍板再決定 Step 6 是否啟動
Step 6  會員系統(預備,需 Step 5 驗證需求才開工)
        - Email + 密碼註冊
        - 個人偏好設定(自選追蹤清單、推播設定)
Step 7  點數錢包 + 金流(預備)
        - 點數預付制取代訂閱制
        - 點數可送、可扣、可退
Step 8  Telegram VIP Bot + Token Gating(預備)
        - 點數兌換限期 Token
        - Bot 驗證 Token 後加入 VIP 頻道
        - Token 過期自動失效
```

**規矩**:
- **Step N 沒驗收通過不准做 Step N+1**
- **Step 5 觀察期是硬性卡關**,沒有真實流量數據前不准動 Step 6+
- 不准跳順序、不准前面沒寫完跑去做後面
- 原 Step 5(TG 推播)、原 Step 6(會員)、原 Step 7(訂閱金流)**已被新版取代**,不要回去看舊版

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

### 使用者頁面 100% 讀本地 DB(2026-04-26 鎖定 — 鐵律)

**任何使用者打開的頁面,資料來源 100% 是本地 SQLite DB,絕不在 request 期間打外部 API。**

#### 為什麼
- **速度**:本地 query 毫秒級,外部 API 可能秒級或 timeout
- **額度**:外部 API 配額有限,被使用者請求拖垮就完蛋
- **穩定**:外部 API 掛掉時我們網站還能運作
- **成本**:不為單一頁面瀏覽消耗 FinMind 配額

#### 例外(僅這兩類可呼叫外部 API)
- `app/services/*_sync.py` 內的同步任務(由排程觸發)
- `/admin/*` 後台手動觸發(Step 6+ 才會有)

#### 範圍
- 所有 `templates/*.html` 對應的 router
- 所有 `/api/*` 公開端點
- 所有 HTMX partial 端點

#### Code review 紅線
任何使用者頁面對應的程式碼出現 `httpx` / `requests` / `urllib` 對外呼叫 = **違規**。
排程同步函數要明確寫在 `services/*_sync.py` 並由 `app/scheduler.py` 觸發。

### 資料範圍(2026-04-26 鎖定 by user)
- **只抓 Taiwan ETF**,不抓個股(後期要擴可再開)
- **歷史長度 5 年**(滾動式,例如今年 2026 → 從 2021-01-01 起算)
- 第一次啟動做完整 backfill,之後每日只補增量
- 來源:**FinMind API**(已申請 token,放在 `.env` 的 `FINMIND_API_TOKEN`)

### 期間報酬計算 = Previous Close 法(2026-04-26 鎖定 — 硬性規則)

**所有期間報酬(1m / 3m / 6m / YTD / 1y / 3y)一律用「期間起點的前一個交易日」收盤當基準。**

#### 為什麼
與 YP-Finance / Yahoo / 大多數金融網站口徑一致。
若用「期間起點當天或之後第一個交易日」(Method B)會跟外部來源差 1~3 個百分點,
客戶比對會疑惑「為什麼跟 Yahoo 不一樣」→ 損害信任。

#### 落地實作
- 共用 helper:`app/services/ranking.py:get_period_base_close(session, etf_id, period_start)`
- 回傳 `(date, adj_close)`:`period_start` 之前最後一個有 `adj_close` 的交易日
- 若 `period_start` 之前沒任何資料(新上市)→ 回 `None`,該 ETF 從排行榜排除
- 大盤 TAIEX(沒 adj_close)用 `get_period_base_close_raw()`,讀 raw close
- 任何 ranking / 績效計算函數**只能透過這個 helper 取基準價**,不准自寫 SQL 撈日期

#### 驗收
- 新方法對 0050 / 0056 / 00981A 等熱門 ETF 算出來的 YTD / 1y / 3y 報酬,
  與 Yahoo Finance / 元大網站對得上(誤差 < 0.05% 為理想)
- 若對不上,先檢查是否「資料源差異」(總報酬 vs 還原股價、配息再投資假設等),不要先改邏輯

### 還原股價(2026-04-26 鎖定 — 不可妥協)

**所有報酬率計算必用「還原股價」(`TaiwanStockPriceAdj`),禁用原始收盤價。**

#### 為什麼
2025-06-18 0050 做過 1:4 分割、2026-03-31 00631L 做過 1:23 分割。
若用原始價算「近 1 年報酬」會出現 -47% / -87% 這種**完全錯誤**的數字。
還原股價自動處理:股票分割、除權息、增資、減資 → 算出來才是客戶真實拿到的報酬。

#### 落地規則
- 排程**同時抓**兩個 dataset:
  - `TaiwanStockPrice` → 原始 OHLCV(用來顯示「目前股價」,跟券商 APP 一致)
  - `TaiwanStockPriceAdj` → 還原 OHLCV(用來算報酬、畫走勢圖)
- DB `daily_kbar` 同時存兩組欄位:
  - `open / high / low / close / volume`(原始)
  - `adj_open / adj_high / adj_low / adj_close`(還原)
- 任何 `(end_close / start_close - 1)` 計算 → **必須用 `adj_close`**,違規視同 bug
- 上線前驗收:抓 0050 過去 1 年 / 3 年報酬,要與 Yahoo Finance / 元大官網對得上

### FinMind API 配額禮讓(2026-04-26 鎖定 by user — 不可妥協)

**FinMind token 是與其他人共用的,必須留 50% 給其他用戶**,絕不獨吞。

#### 規則
- **單小時用量上限 = `api_request_limit_hour` × 50%**
  (例:Sponsor 6000/hr → 我們最多 3000/hr,剩下 3000 留給其他人)
- 任何 batch 操作(backfill / 排程 sync)前後**必須查 quota**
- 接近 50% 紅線(例如 ≥ 45%)就**主動暫停**,sleep 到下個整點再繼續
- **不准** spike(短時間 burst)— sync 一律加 throttle(每筆請求最少間隔 N 秒)

#### 落地實作
- `app/services/finmind.py` 必須提供:
  - `check_quota() -> dict`:打 `/v2/user_info`,回 `{used, limit_hour, ratio, room}`
  - `request(...)`:統一 wrapper,內建 throttle + 自動檢查紅線
- 任何外部 API 呼叫**只能走 `finmind.request()`**,不准散落在各檔案直接打 httpx
- 排程 sync 開始前 log 一次 quota,結束再 log 一次,差值寫入 log
- 接近紅線 → log warning + sleep 到下一個 :00 整點 + 自動繼續

#### 監控節奏建議
- backfill(252 ETF × 2 dataset = 504 請求)→ throttle 設 1.0 秒/筆,約 8.5 分鐘跑完,峰值用量 ~10%
- 每日增量(日新增 504 請求)→ 同樣 throttle,跑完 ~8.5 分鐘,佔小時配額 ~10%
- 任何時候**單小時實際用量不得超過 3000**(假設 limit 6000)

### ETF 名單來源(2026-04-26 鎖定 by user)

**不准 hardcode ETF 清單,必須從 FinMind 動態抓全市場。**

#### 為什麼
新 ETF 不斷上市(主動式 ETF 在 2024 才開放,2025-2026 數量爆發),
寫死清單 = 客戶看不到新 ETF = 「別人用沒意義」(user 原話)。

#### 落地規則
- 啟動 + 每日 14:30 排程都先 sync `etf_list`(從 FinMind `TaiwanStockInfo` 撈所有 `industry_category=ETF`)
- 全市場約 252 支全部入庫,**不過濾**
- 分類用程式規則(代號 ending + 名稱關鍵字),分類表存在 `app/services/etf_classifier.py`,**不寫死進 JSON**
- 排行榜 UI 預設只顯示 3 大類(主動 / 市值 / 高股息),其他類別(海外 / 主題 / 槓桿反向 / 債券)藏在進階,後期需要再開
- 大盤指數 TAIEX 也存在同一張 `etf_list`,分類欄位標 `index`(只當對比基準,不出現在排行榜)

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

### Idiot-Proof Principle — 白癡也會用(永久紀律 / 2026-04-26 鎖)

> 「我主要是要讓客戶感覺到淺顯易懂,白癡也會用的感覺。」— user 原話

#### 核心定義
所有 UI / 數字 / 文案,要做到使用者**看一眼就懂,不用思考**。

#### 永久禁忌
1. **不准在介面上出現需要解釋的專有名詞**(列表會擴增,看到就加)
   - ❌ 夏普值、Sortino Ratio、Beta 係數、Alpha、標準差、波動率、最大回撤
   - ❌ 年化、複利、再投入、除權息、回測
   - ✅ Step 3 進階比較頁可以有,**但必須用 hover tooltip 出現一句中文白話解釋**(下方紀律)

2. **不准用「需要點 ? 看註解」的數字呈現**
   - ❌ 排行榜旁邊小字「※ 本網站使用 adjusted close 計算」
   - ✅ 數字直接跟券商 APP 一樣,客戶不必看註解就能信

3. **不准用工程術語當錯誤訊息**
   - ❌「API rate limit exceeded」「Database timeout」「500 Internal Server Error」
   - ✅「資料更新中,請稍候」「目前繁忙,1 分鐘後再試」

4. **文案用詞 = 我媽看得懂程度**
   - ❌ 績效歸屬分析、滾動報酬、再平衡
   - ✅ 為什麼漲/跌、過去一年表現、多久整理一次

#### Jargon-Tooltip 規則(無法避免時)
- 進階頁面(如 Step 3 績效比較)若必須出現「夏普值」這類名詞:
  - **必須加 hover/tap 出現的提示**
  - **提示限一句**(15~30 字以內),不要寫成段落
  - 範例:`夏普值:每承擔 1 分風險可換到的報酬,越高越好`
  - 範例:`Sortino Ratio:跟夏普值類似,但只算「往下跌」的風險`
  - 範例:`年化波動率:這支 ETF 每年大約上下擺多大`

#### 範圍
- 所有 `templates/*.html`、所有錯誤訊息、所有提示文字
- 例外:`CLAUDE.md` / `README.md` / 後台 admin 介面可使用專業術語

### ETF 類別白話副標(永久紀律 / 2026-04-26 鎖)

排行榜各區塊大標題下,**必須**有一行極簡白話副標(text-gray-400 / text-sm):

| 類別 | 副標(逐字使用,不准改) |
|------|------------------------|
| 市值型 | 買到台股市值最大的公司 |
| 高股息 | 主打配息,適合存股族 |
| 主題型 | 鎖定特定產業(半導體、AI、5G 等),電腦按指數選股 |
| 主動式 | 基金經理人選股,可彈性調整持股 |
| 槓桿/反向(高風險) | 短線操作工具,長期持有可能虧損 |
| 債券型 | 公債、公司債,波動較小 |

> 海外型(2026-04-26 by user):
> - **首頁不做獨立區塊**(沒有「海外 Top 10」推薦)
> - 資料**仍進 DB,可被搜尋與查詢**
> - 「近月最火」**仍可包含海外**(它是跨類別,誰漲多誰上)
> - Step 3 個別 ETF 查詢可支援海外

理由:主動式跟主題型容易混淆,白話副標就是 Idiot-Proof 落地。

### 視覺尺寸 — 不戴老花眼鏡也能讀(永久紀律 / 2026-04-26 鎖)

**字體與留白都以「中年使用者不需放大就能輕鬆讀」為基準。**

#### 字體規則
- 表格 `<td>` 內文:`text-base`(16px)以上 — 不准 `text-sm`
- 數字欄(收盤、報酬、vs 大盤):`text-base sm:text-lg` + 等寬 `font-mono`
- 表格 `<th>`:`text-base` 或 `text-sm`(配 `uppercase` 也 OK)
- 區塊大標題:`text-xl sm:text-2xl`(維持現有)
- 副標:`text-sm sm:text-base`,顏色 `text-muted` (`#9ca3af`)
- 「對照大盤」這類補充行:`text-sm sm:text-base` + 數字 `font-semibold`,**不能比表格內文還小**

#### 排版規則
- 表格 row 至少 `py-3`(垂直 padding 12px),不要擠
- 數字一律 `tabular-nums`(等寬)以小數點對齊
- 報酬率欄位**強制兩位小數**,讓 +5.10% 跟 +12.30% 對齊

### UX 哲學 — 簡單清晰雙重確認(永久紀律 / 2026-04-26 鎖)

**目標客戶 = 一般散戶,不是分析師、不是工程師。**
所有畫面必須通過「我媽看得懂」測試。

#### 四個原則(縮寫:**簡淺雙滲**)

1. **簡單顯示**
   - 一個畫面**只解決一個問題**,不要把 5 件事擠在一頁
   - 排行榜只顯示「該類別 + 該時間」一張表,不要 9 張同時開
   - 數字不要超過 3 位小數,大數用 K/M/億 縮寫

2. **淺顯易懂**
   - 不准用專業術語(Sharpe Ratio / 標準差 / β 值 / 還原 / 復權...)
   - 「期間報酬率 +12.3%」要寫成「**這段時間賺 12.3%**」
   - 顏色直觀:贏大盤 = 紅、輸大盤 = 綠(台股習慣)
   - 篩選用「**主動式 / 市值型 / 高股息**」這種日常詞,不要「Active / Passive / Dividend Yield」

3. **雙重確認**
   - 任何數字要呈現給客戶前,**必先用真實 API 驗證一次**(不要憑印象寫死預設值)
   - 抓資料來源時,**FinMind 沒有的欄位就不要假裝有**(寧缺勿造)
   - 報酬計算**強制用還原股價**(見「資料抓取原則」)
   - 上線前用真實案例反推驗證(例如 0050 過去 1 年報酬要對得上 Yahoo / 元大網站)

4. **滲透度高**
   - 不要登入才能看(排行榜、新聞牆都是公開頁面,Step 6 才會有會員牆)
   - 第一次進站 3 秒內就要看到價值,**不要 onboarding tour、不要彈窗、不要註冊牆**
   - 手機優先(見「響應式設計」)
   - 載入快:純讀本地 SQLite,不要前端等 API call

#### 對應到現有設計
- 排行榜頁面:**永遠只顯示一張表**(用 tab/下拉選類型 + 時間)
- 圖表:**最多 5 條線**(超過要可勾選顯示),否則改用色塊或 sparkline
- 任何欄位不確定客戶看不看得懂 → **加副標說明**(灰色小字)

---

### 響應式設計 — 手機版排版同等重要(永久紀律)

**桌機跟手機**雙端都要排版漂亮。台灣使用者大多用手機看股票/ETF,
**手機體驗壞 = 整個產品失敗**。不准「桌機優先、手機隨便」。

#### 強制規則
- 採 **mobile-first**:預設樣式給手機,大螢幕用 `sm:` `md:` `lg:` 加強
- 每個頁面**必須**在 375px(iPhone SE)寬度下能正常使用
- 文字最小 14px,點擊區最小 44×44px(Apple HIG 觸控標準)
- 不准出現「手機要橫向捲」(`overflow-x-auto` 只允許大表格用)
- 不准把桌機 navbar 直接搬到手機(必要時用漢堡選單)

#### 元件適配對照
| 元件 | 桌機 | 手機 |
|------|------|------|
| 卡片網格 | 多欄 `md:grid-cols-3` | 單欄 `grid-cols-1` 堆疊 |
| 大表格 | 完整顯示 | 橫向捲動 + sticky 第一欄,或改卡片列表 |
| 圖表 | 寬版 | 縮高度、字級調小、隱藏次要 series |
| Navbar | 全部選項展開 | 漢堡選單(Alpine 控制) |
| 數字 | 大字級 | 自動縮小但仍清晰 |

#### 驗收標準
每個 Step 完成前,**必須**用瀏覽器開發者工具切到 iPhone SE(375×667)模式檢查:
- 沒有水平捲軸(除非是大表格)
- 文字不擠在一起、不溢出
- 按鈕點得到、選單打得開

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

### dividend(Step 2 加入)
```
id (PK)
etf_id (FK → etf_list.id)
ex_date (除息日)
cash_dividend (現金股利,每股)
stock_dividend (股票股利,每股)
payment_date (預計發放日)
announce_date (公告日)
fiscal_year (會計年度,民國年)
INDEX (etf_id, ex_date)  ← 複合索引
```

### 之後會加(Step 6+ 商業模式相關)
- **`members`** — 會員主檔(email、bcrypt 密碼、暱稱、註冊日)
- **`wallet`** — 點數錢包(member_id、balance、updated_at)
- **`point_transaction`** — 點數交易紀錄(贊助購買、贈送、扣抵、退費,每筆 audit log)
- **`access_token`** — VIP Token(member_id、token、issued_at、expires_at、revoked_at、last_used)
- **`subscription_product`** — 可購買的產品定義(例如「100 點 = 30 天 TG VIP Token」)
- **`referral`** — 推薦關係追蹤(referrer_member_id、referee_member_id、reward_points)
- **`audit_log`** — 金流相關 critical action 全部留紀錄(供退費糾紛舉證)

> 上述 table 在 Step 6/7/8 才會建,**Step 2~5 不做**,寫在這裡是預留設計藍圖。

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

# 🚀 上線前必做清單(Pre-Launch Checklist)

> 等 Step 7 訂閱金流完成、正式對外開放前 1~2 週做。
> **不要現在做**,寫在這裡是讓未來 Claude 不會漏。

## 法律文件(三大件,缺一不可)

1. **免責聲明** — 投資自負盈虧、資料僅供參考、不構成投資建議
2. **隱私權政策** — Cookie / 個資處理 / 第三方追蹤(GA、Meta Pixel 等)
3. **使用條款** — 網站使用規則、禁止行為、爭議處理管轄

## 該做(強烈建議)

4. **智慧財產權聲明** — 防抄襲(內容、UI、資料庫架構皆受保護)
5. **資料來源聲明** — FinMind / 台灣證交所 / 櫃買中心等正式註明

## 進階(B2B / 訂閱要用)

6. **商業授權條款** — 給未來 B2B 變現留伏筆
7. **訂閱服務條款** — 會員 + 金流條件、自動續訂、終止
8. **退費政策** — 金流上線必備(信用卡爭議、消保法 7 天鑑賞期)

## 執行步驟

1. user 起草初稿(可參考 YP-Finance 法律文件結構,已存 user 本地)
2. 找專做網站/SaaS 法律的律師審(預算 NTD 5,000 ~ 10,000)
3. 律師定稿後上線
4. footer 加 3 個獨立頁面連結:
   - `/disclaimer`(免責)
   - `/privacy`(隱私)
   - `/terms`(條款)

## Git 推上 GitHub 前必審

> 推 GitHub(任何 remote)前**必審 git log**,確認以下都乾淨:

- [ ] 所有 commit author 都是 placeholder(`dev@etfwatch.local`),**沒有 user 私人信箱**
  - 檢查:`git log --pretty=format:"%h %an <%ae>" | grep -iE "a35615666|c8c886|allenac|gmail.com"` 應無輸出
- [ ] 沒有 `.env` / `.env.local` 等含 token 檔案被 commit
  - 檢查:`git log --all --full-history -- .env` 應無輸出
- [ ] 沒有 FinMind token / API key 寫死在程式碼或文件
  - 檢查:`git grep -i "eyJ0eXAi\|FINMIND_API_TOKEN=ey"` 應無輸出
- [ ] 沒有 SQLite db 檔(`*.db`)被 commit
  - 檢查:`git ls-files | grep -E "\.db$"` 應無輸出
- [ ] 若任一條 fail,**先處理乾淨再推**(可能要 `git filter-repo` 改寫歷史)

---

# 📝 進度

(每完成一個功能更新)

- [x] Step 1:骨架(FastAPI + SQLAlchemy + 首頁) ✅ 2026-04-26
  - 路徑:`app/main.py` `app/config.py` `app/database.py` `app/models/*` `app/routers/*` `app/scheduler.py` `templates/*` `run.py`
  - 啟動:`python run.py` → http://127.0.0.1:8000(暗色首頁)/ /api/health 回 `{"status":"ok","db":"ok"}`
  - DB:`data/etf.db` 自動建立,三表 `etf_list` / `daily_kbar` / `news` 已就緒
  - 排程:AsyncIOScheduler 啟動後立即跑一次 heartbeat,之後每 60 秒一次
- [x] Step 2:ETF 排行榜 ✅ 2026-04-26
  - [x] ETF universe sync(252 支 ETF + 自動分類)
  - [x] K-bar sync(raw + adj OHLCV,5 年 backfill + 每日增量)
  - [x] TAIEX 大盤當對比基準
  - [x] 6-section 首頁(近月最火 + 主動 + 市值 + 高股息 + 槓桿 + 反向)
  - [x] vs 大盤欄位 + 紅綠標示(台股習慣)
  - [x] 14:30 自動更新排程(取代 heartbeat)
  - [x] **B 公式 Total Return**(raw close + 期間累積現金股利)
  - [x] **dividend table** + 每日 14:30 sync
  - [x] **網站全面改名**:ETF 觀察室 · ETF Watch
  - [x] **Stealth Branding 浮水印**(全頁背景 + 每張卡片右下角 ETF Watch)
  - [x] **法律文件**:免責聲明 / 使用條款 / 隱私權 + footer 小字連結
  - [x] **anomaly filter**(±200% / -80% 自動排除,防 reverse split 污染)
  - [x] **ranking_section macro 重構**,6 區塊統一渲染
  - [x] 0050 對 YP YTD 驗證 PASS(0056 / 00981A FAIL,屬資料源差異)
- [ ] Step 2.5:ETF 詳情頁 `/etf/{code}`(7 區塊,**100% 讀本地 DB**)
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

### 2026-04-26 / Step 2 / 「同步改 + 雙重確認」鐵律(user 指令立法)

> 「改一個區域 其他區域也要跟著改 而且要雙重確認」— user 原話

#### 規則
- **同步改**:任何重複出現於多區塊/多檔的元素(行內字串、文案、樣式、URL、欄位順序)
  改動時**必須**同時更新所有出現位置
- **抽 macro / partial**:同樣結構出現 ≥ 2 次 = 一定要抽,禁止 inline 散在多處
  (避免「改一處忘了其他處」的物理可能性)
- **雙重確認**:改完後**用 grep 或自動化 script** 列出所有出現位置,人類肉眼比對一次
  (不能只信「我覺得我改完了」)

#### 已踩過的坑
- 排行榜 6 區塊 inline 寫死 → footer 文案改一個忘了其他 5 個 → user 抓到
- 「對照大盤」per-section 顯示 6 次 → user 要求合併到頂部「市場概況」一處

#### 落地清單
- `templates/_partials/ranking_section.html` + `ranking_table.html`(已抽)
- `_common_ctx()` 在 `pages.py`(brand 變數一處改全頁更新)
- 任何 router 增加 template variable,**所有 template 必須一致使用**(brand_zh / brand_en / brand_full)

### 2026-04-26 / Step 2 / 私人信箱差點被寫進 git author(嚴重踩坑)
- 我準備 commit Step 2 時用 `git -c user.email=<user 私人信箱>` 嘗試提交
- user 主動發現並阻止 → 強制改用專案 placeholder 信箱
- **永久鐵律(寫進「Git 紀律」也不嫌多)**:
  1. **任何 commit 前**,先 `git config --local user.name "ETF Watch Dev"` + `user.email "dev@etfwatch.local"`
  2. **不准** 用 `git -c user.email=...` inline 蓋 config(難稽核、易誤用)
  3. **不准** 用 user 提供的私人信箱(a35615666 / c8c886 開頭)做 commit author
  4. 第一次進入新專案 / 新環境 → 立刻檢查 `git config --local user.email`
- ⚠️ Step 1 commit (c664e71) 因為這次踩坑前已用了私人信箱,**git 歷史已污染**
  - 處理方案待 user 拍:
    a. 接受污染(若不上 GitHub 公開 repo)
    b. 推前用 `git filter-repo` / `git rebase -i` 改寫 author(會變更所有 hash)

### 2026-04-26 / Step 2 / 重複渲染區塊禁止 inline 寫死(踩坑後立法)
- 排行榜 6 個區塊原本 inline 在 index.html 寫死,改 footer 文案時只動到第一個,其他遺漏 → user 抓到不一致
- **新規則**:同樣結構的 UI 區塊出現 ≥ 2 次,**必須**抽 Jinja2 macro / partial
- 已落地:`templates/_partials/ranking_section.html` + `ranking_table.html`,index.html 用 `{% from %}` import
- 教訓:UI 一致性靠 single source of truth,不能靠 grep 跟眼睛

### 2026-04-26 / Step 2 / B 公式驗收:0050 PASS,0056 / 00981A FAIL(資料源差異)
- 驗收標準:跟 YP-Finance 對 YTD 報酬,誤差 < 0.5pp 為 PASS
- 結果:
  - 0050 → 我們 +38.64% / YP +38.17% / 差 **0.47pp** ✅ PASS
  - 0056 → 我們 +13.85% / YP +18.72% / 差 **4.87pp** ❌ FAIL
  - 00981A → 我們 +64.40% / YP +72.47% / 差 **8.07pp** ❌ FAIL
- 0056 在 DB 有 13 筆完整配息歷史,FAIL 不是配息漏抓
- 00981A 無配息(主動成長型),FAIL 純粹 raw close 對不上
- **結論:資料源差異**(FinMind vs YP-Finance),非程式 bug
- 後續若要追平,需改抓不同來源(MoneyDJ / Yahoo)或請 user 提供 YP API 端點逆向公式
- 目前先以 B 公式(我們的口徑)上線,UI 寫明「如與券商略有差異以您券商為準」

### 2026-04-26 / Step 2 / 反向 ETF 合併單位(reverse split)讓 B 公式失效
- 00674R 在 2026-04-22 raw close 從 5.18 跳到 25.90(+400%),這是 5:1 reverse split
- B 公式用 raw close 算就被分割污染,排行榜跑出 +415% 假數字 → user 抓到
- 短期修法:**異常值防護網**(`ranking._is_anomalous()`)— |return| > +200% 或 < -80% 自動排除 + log warning
- 長期修法:B 公式應自己計算 split factor,或在 dividend 同步時也抓「合併/分割」事件;短期不做
- 教訓:反向/槓桿 ETF 偶爾會做合併單位「拉抬」名目股價,任何用 raw close 的計算都要防

### 2026-04-26 / Step 2 prep / 0050 / 00631L 用原始價算報酬 = 災難
- 0050 在 2025-06-18 做了 1:4 分割,raw close 從 188.65 跌到 47.57(-74.8%)
- 00631L 在 2026-03-31 做了 1:23 分割,raw close 從 443.15 跌到 19.26(-95.7%)
- 用原始價算近 1 年報酬:0050 = -47%、00631L = -87%(**完全錯誤**)
- 用還原價算:0050 = +121%、00631L = +185%(才是真實表現)
- 教訓:**任何報酬率計算只能用 `TaiwanStockPriceAdj`(還原股價)**,違反就是 bug,不接受任何例外

### 2026-04-26 / Step 2 prep / 主動式 ETF 數量我自己亂報
- 我憑印象說「主動式只有 5 支」,實際 FinMind 撈出來有 21 支(00980A ~ 00997A)
- 高股息我說 13 支,實際 25 支
- 教訓:**回答數量、清單問題前,先打 API 驗證**,不要靠 LLM 記憶

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

### 2026-04-26 / Step 2 / ETF 名單動態抓 vs 手動 curate
- 考量:手動 curate 30 支控制品質,但「新 ETF 上市看不到」= 客戶價值流失
- user 原話:「能更新最新資訊,別人用才有意義」
- 決定:**全 252 支 ETF 全部入庫**,分類用程式規則自動跑(代號 ending + 名稱關鍵字)
- 排行榜預設只給 3 大類(主動 / 市值 / 高股息),但其他類別已在 DB,後期 UI 加 tab 即可

### 2026-04-26 / Step 2 / 網站命名定案:ETF 觀察室 · ETF Watch
- 中文主名:**ETF 觀察室**
- 英文副名:**ETF Watch**(同時是浮水印 Stealth Branding 用字樣)
- 完整品牌字樣:`ETF 觀察室 · ETF Watch`
- meta description:台灣 ETF 排行、績效比較、即時新聞,看 ETF 最直覺的觀察室
- meta keywords:台灣 ETF, ETF 排行, ETF 比較, ETF 觀察室, ETF Watch, 台股 ETF
- 任何寫死字串 `Taiwan ETF Dashboard` 一律換掉,設定值改自 `settings.app_name`

### 2026-04-26 / Step 2 / 暫不做付費版 — 改為 Step 5 觀察期
- 原計畫 Step 6(會員)、Step 7(訂閱金流)**從硬性開發順序中移除**
- 改為 **Step 5 觀察期**(1~2 個月):免費版完整上線 → 加 GA4 → 累積流量 → 看使用者反應再決定變現
- 理由:先把觀察體驗做到位,沒人用之前談變現是空話

### 2026-04-26 / Step 2 / 商業模式定案 — Token Gating(替代訂閱制)

> 取代原本「訂閱金流」設計。完整 8-Step 開發路線見「硬性紅線 — 開發順序」。

#### 核心架構
1. **免費區**(Step 1~4 全部做完)
   - 排行榜 / 比較圖 / 新聞,**完全公開**
   - 不要登入牆、不要付費牆,流量入口全敞開
2. **會員註冊**(Step 6)
   - Email + bcrypt 密碼,**免費註冊**
   - 個人偏好設定(自選追蹤、推播設定)
3. **點數系統 / 預付制**(Step 7)
   - 會員贊助購買點數(例:100 點起跳,可分多種價位)
   - 點數記在 `wallet` table,**可送、可扣、可退**
   - 每筆異動寫入 `point_transaction`(audit log,供退費舉證)
4. **Token 兌換 / VIP 入場券**(Step 8)
   - 用點數兌換**限期 Token**(例:100 點 = 30 天 Token)
   - Token = 隨機字串,寫進 `access_token` table(含 expires_at)
   - 會員拿 Token 私訊 Telegram Bot → Bot 驗證後加入 VIP 頻道
   - **Token 過期自動失效**,可主動 revoke 防盜

#### 行銷玩法(Step 8 內含)
- 註冊送 50 點(體驗用)
- 推薦朋友雙方各送 100 點(`referral` table 追蹤)
- 節日活動送點 / 連續登入送點

#### 為什麼這樣設計(替代訂閱制)
| 痛點 | 訂閱制(被取代) | 點數預付(採用) |
|-----|----------------|----------------|
| 扣款失敗 | 用戶不知道何時、平台收不到錢 | 預付完成,無扣款風險 |
| 退款糾紛 | 信用卡爭議多,商家被銀行扣款 | 購買即消費完成,法律保護單純 |
| 產品擴充 | 一次只能賣「一個訂閱方案」 | 點數可組合多種商品(月票、季票、單次活動) |
| 群組踢人 | 訂閱到期手動踢,易遺漏 | Token 過期 = 進不了門,精準 |
| 防盜帳 | 一個帳號多人共用難擋 | Token 一次性綁定,可主動 revoke |

#### 落地紀律
- 上述 schema 與 service **僅在 Step 6/7/8 才建**,Step 2~5 嚴禁先做
- 點數錢包、Token 任何異動**必寫 audit log**(`point_transaction` / `audit_log`)
- 金流相關**永遠保留人工確認**(已在「安全紅線」)
- 退費政策必須在 Step 7 上線前完成(已在「上線前必做清單」)

### 2026-04-26 / Step 2 / Total Return 走方案 2(B 公式 + 配息再投入)
- 4 種公式對 user 預期 38.17% 的差距:
  - A 純 raw 比值 → 37.12%(差 1.05pp)
  - **B raw + 加回現金股利 → 38.64%(差 0.47pp,< 0.5pp)** ✅ 採用
  - C 配息再投入 at ex-day close → 39.03%(差 0.86pp)
  - D FinMind adj_close 直接 → 39.05%(差 0.88pp)
- 公式定義:`return = (期末 raw close + 期間累積現金股利) / 期初 raw close - 1`
- 共用 helper:`ranking.apply_b_formula(session, etf_id, period_start)`,**所有報酬計算只能透過此 helper**
- 期間起點仍用 Previous Close(前一交易日收盤)
- 新建 `dividend` table 儲存現金股利,排程每日 14:30 跟 K 棒同時同步

### 2026-04-26 / Step 2.5 預備需求 — ETF 詳情頁(等 Step 2 收尾後再做)

> ⚠️ 不要現在動手,Step 2 還沒結束。寫在這裡是 Step 2.5 開工時直接照做。

#### 入口與網址
- 排行榜每筆 ETF 代號連結 → `/etf/{code}`
- 鐵律:**100% 讀本地 DB**(見「使用者頁面 100% 讀本地 DB」)

#### 7 個區塊(全用 macro / partial,禁止 inline)
1. **基本資訊卡** — 代號 / 全名 / 類別 / 發行商 / 成立日 / 管理費 / 淨值 / 規模
2. **績效快照 + 近 1 年走勢圖** — K 棒折線 + TAIEX 對照(ECharts 暗色,中央 ETF Watch 浮水印)
3. **經理人介紹**(Phase 2 由人工/AI 補)
4. **選股理念**(Phase 2 由人工/AI 補)
5. **十大持股** — 每月快照,持股可點查近年走勢(進階,可後做)
6. **產業分布**(圓餅圖,中央 ETF Watch 浮水印)
7. **配息歷史** — 從 `dividend` table 直接查

#### DB 新增 table
- `etf_detail`:每支 ETF 一筆基本資訊 + 經理人 + 選股理念
- `etf_holding`:每支 ETF 多筆持股快照(每月一份)

#### 實作分 3 階段
- **Phase 1**(自動撈):基本資訊 / 績效 / 持股(FinMind 有的) / 產業分布 / 配息
- **Phase 2**(人工 + AI):經理人 / 選股理念
  - admin 後台 `/admin/etf/{code}/edit`
  - 整合 Claude API(貼說明書 URL → AI 摘要 → 人工審核 → 入庫)
  - 每天 5~10 支熱門優先
- **Phase 3**(可選):爬蟲補滿冷門 ETF

#### UI 紀律(沿用 Step 2 全部規矩)
- 暗色系 / 卡片式 7 區塊 / ETF Watch 浮水印 / 無 emoji / 白話副標
- ECharts 圖表複用 `static/js/chart-watermark.js`(opacity 0.05、字級大、不影響閱讀)

### 2026-04-26 / Step 2 / 浮水印系統 = Stealth Branding(增長策略)
- 目的:使用者**截圖分享時**,品牌名跟著傳播,被動曝光
- B1 全頁背景:SVG inline + CSS background-image,「ETF Watch」-30 度斜散布,opacity 0.04,字色 `#1f2937`
- B2 每張排行卡片右下角:「ETF Watch」字樣,`text-xs text-gray-700 opacity-30`,`absolute bottom-3 right-4`
- B3 ECharts 圖表中央(Step 3 用):helper `static/js/chart-watermark.js`,opacity 0.05,字級 60px
- B4 未來匯出 PNG / 截圖:用 html2canvas + canvas drawText 加水印 + 網址(Step 3 後啟用)
- **必須**:`pointer-events: none`,不擋 hover、不影響選取與互動

### 2026-04-26 / Step 2 / Total Return 自實作之路 — 探勘階段(待 user 拍方向)

#### 探勘結論(FinMind 沒現成 Total Return 給 ETF)
- `TaiwanStockTotalReturnIndex` → 對 ETF 回 0 rows(可能只給指數)
- `TaiwanStockReturn / TaiwanStockTotalReturn / TaiwanStockReturnIndex / TaiwanStockPriceTotalReturn` → 422 不存在
- ✅ `TaiwanStockDividend` 有用:給每支 ETF 的歷年配息(ex-date / cash / stock / payment date)
- ✅ `TaiwanStockDividendResult` 有用:給除息日前後 reference price + 實際 dividend

#### 0050 YTD 算法 4 種對比(user 預期 38.17%)
- A 純 raw close 比值:**+37.12%**(差 1.05pp)
- B raw + 加回現金股利(不再投入):**+38.64%**(差 0.47pp)← 最接近,但仍超出 0.05% 門檻
- C 配息再投入 at ex-day close:**+39.03%**(差 0.86pp)
- D FinMind adj_close 直接比:**+39.05%**(差 0.88pp)
- **沒有任何標準公式精準對到 38.17%**,差異方向不一致 → 強烈推測 user 來源(YP-Finance)用獨家算法或不同資料商

#### 待 user 拍方向(三選一)
1. **接受 ~0.5~1pp 誤差,用 D adj_close**(目前實作,最簡單)
2. **接受 ~0.5pp 誤差,用 B raw+加回 cash dividend**(需新建 dividend table + 同步)
3. **跟 user 確認 YP-Finance 來源,逆向找出公式**(可能需要 user 提供截圖或 API)

#### 若採方案 2 或 3 的 schema 預備
- `daily_kbar` 加欄位:`total_return_close: float | None`
- 新 table `dividend`:
  - id, etf_id, ex_date, cash_dividend, stock_dividend, payment_date, announce_date, INDEX(etf_id, ex_date)
- 新 service:`app/services/dividend_sync.py`
- 排程加每日 14:30 的 dividend sync(跟 K 棒並行)

### 2026-04-26 / Step 2 / 期間報酬基準改 Previous Close 法(取代 Method B)
- 第一版用 Method B(期間起點當天或之後第一個交易日),user 校對發現跟 YP-Finance 差 1~3%
- 改成 Previous Close 法(期間起點的**前一個交易日**收盤當基準)
- 通用 helper:`ranking.get_period_base_close(session, etf_id, period_start)`
- 任何 ranking / 績效計算只能透過此 helper 取基準價,不准自寫 SQL 撈日期
- 驗收結果(2026 YTD,以 41 支已 backfill ETF 計算):
  - 0050: 我們 +39.05% / user 預期 ~+38.17% (差 0.88pp)
  - 0056: 我們 +16.83% / user 預期 ~+18.72% (差 1.89pp)
  - 00981A: 我們 +67.77% / user 預期 ~+72.47% (差 4.70pp)
- 程式邏輯已驗證(對 FinMind 直查 0% 差),數字差異**方向不一致** → 推測 user 來源是不同 dataset(可能含再投資的 Total Return Index,但 FinMind 無對應 ETF 資料)
- 後續若客戶質疑,先檢查資料源差異,不要先改邏輯

### 2026-04-26 / Step 2 / Default-Safe, Optional-Powerful 原則(全 Step 沿用)

> 「**預設介面保守保護新手,進階介面信任專業使用者**」

#### 場景對照
| 介面類型 | 例子 | 槓桿/反向處理 |
|---------|------|--------------|
| **被動瀏覽**(預設介面) | 排行榜首頁、推薦清單、新聞牆 | **嚴格隔離**,槓桿/反向獨立區塊+警示文字 |
| **主動查詢**(進階介面) | 績效比較、自選圖表、自定篩選 | **完全開放**,使用者主動選 = 他知道風險 |

#### 落地細則(Step 2 排行榜)
1. **「近月最火」綜合榜** → 槓桿/反向**禁止列入**
2. **各類別排行**(市值/高股息/主動/主題/海外/債券)→ 槓桿/反向**不混入**
3. **獨立區塊「槓桿型 / 反向型(高風險)」**
   - 位置:首頁**最下方**,不放顯眼位置
   - 上方灰色警示文字:「槓桿/反向型適合短線操作,長期持有可能因波動衰減導致虧損」
   - 視覺標記:代號旁小標籤「2 倍」「-1 倍」,amber 色 `#f59e0b`
   - 區塊整體用**淺暖色邊框**暗示風險
   - **絕對禁止 emoji**(沿用 CLAUDE.md 鐵律)

#### 落地細則(Step 3 績效比較,等開工再用)
1. 槓桿/反向 ETF **可被任意選入比較**
2. ECharts 線形差異化:
   - 一般 ETF → 實線
   - 槓桿型 → 粗實線(`lineStyle.width` 加大)
   - 反向型 → 虛線(`lineStyle.type='dashed'`)
3. legend 顯示「00631L (2 倍)」「00632R (-1 倍)」標明屬性
4. hover tooltip 顯示報酬即可,不必標 beta 倍數

### Step 3 預備需求(2026-04-26 by user / Step 2 完成後再開工)

> ⚠️ 不要現在動手,Step 2 還沒結束。寫在這裡是 Step 3 開工時直接照著做。

#### 功能
1. 上方:多 ETF 選擇 + 自訂日期區間(start ~ end)
2. 中間:績效統計表
   - 總報酬率
   - 年化報酬率
   - 最佳年 / 最差年(年度報酬)
   - 年化波動率
   - 夏普值(Sharpe Ratio)
   - Sortino Ratio
   - **報酬一律用 adj_close 計算,跟排行榜一致**
3. 下方:累積報酬率折線圖(ECharts 暗色系)
   - X 軸:日期
   - Y 軸:累積 % 報酬
   - 多條線同時顯示
   - hover 顯示對應日期跟數字

#### 風格
- **嚴格不抄 YP-Finance 視覺**(白底藍 header)
- 用我們自己的暗色系 `#0a0e1a / #131829`
- 卡片式佈局,不用表格藍色橫向 header
- ECharts 用暗色系預設,線條用我們的紅綠配色
- 響應式:桌機/手機都漂亮(見「響應式設計」紀律)

### 2026-04-26 / Step 2 / 首頁排版改 4-section 直接呈現,不做 tab 篩選
- user 原話:「越簡單 越透明 越好」「滲透度高」
- 拋棄 tab 切換期間/類別的設計(點擊負擔太重)
- 改成首頁直接 4 個 section,所有結論一眼看完:
  - Section 1:**近月最火 ETF**(全市場跨類別,1 個月報酬 Top 10)
  - Section 2:**主動式**(近 3 個月 Top 10)
  - Section 3:**市值型**(近 3 個月 Top 10)
  - Section 4:**高股息**(近 3 個月 Top 10)
- 每 section 底部有「查看全部」連到詳細頁(Step 3 再做)
- 報酬一律用 adj_close 算,vs 大盤 = ETF 報酬 - TAIEX 報酬

### 2026-04-26 / Step 2 / 「對比加權指數」= 對比 TAIEX 大盤,非加權平均
- user 第一次說「對比加權」我誤會成「規模加權平均報酬」(算法層次)
- 實際 user 意思是「對比加權指數(TAIEX)」(基準線層次)
- 決定:每支 ETF 各算各的、各自跟 TAIEX 比;**不做任何 ETF 平均運算**
- 表格欄位:期間報酬 / vs 大盤(=該 ETF 報酬 - TAIEX 報酬)
- 圖表:大盤 1 條灰粗線 + 該類別每支 ETF 1 條細線(超過 5 條要可勾選顯示)

### 2026-04-26 / Step 1 / Tailwind 配色寫進 base.html 而非獨立 CSS
- 考量:CLAUDE.md 規定 Tailwind 用 CDN 不跑 npm build,自訂顏色只能靠 `tailwind.config`
- 決定:把暗色配色 token(bg/card/border/fg/muted/up/down/accent)寫在 `base.html` 的 `tailwind.config` 區塊
- 好處:每頁繼承自動拿到,不用手動引 CSS;改色一次改完

---

# 📅 Session 歷程

> 每次任務完成自動往下加一行,**最新的在最下面**。

2026-04-26 09:30 | Step 1 骨架 | ✅ | FastAPI + SQLAlchemy 2.0 + SQLite + AsyncIOScheduler + 暗色首頁全部就緒,/api/health 與 heartbeat 皆驗證通過
