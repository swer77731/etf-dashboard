# 給 Claude Code 的第一輪指令

## 操作步驟

### Step 1:建立專案資料夾

```cmd
mkdir C:\projects\etf_dashboard
cd C:\projects\etf_dashboard
```

(路徑你自己決定,我用 `C:\projects\etf_dashboard` 當例子)

### Step 2:把 CLAUDE.md 放進去

下載我給你的 `CLAUDE.md`,放到 `C:\projects\etf_dashboard\CLAUDE.md`

### Step 3:啟動 Claude Code

```cmd
cd C:\projects\etf_dashboard
claude
```

### Step 4:複製下面這段,貼進 Claude Code

```
我要做 Taiwan ETF Dashboard,需求都寫在 CLAUDE.md。

【現在開工 Step 1:骨架】

做這幾件事:

1. 建立完整資料夾結構(照 CLAUDE.md 寫的)
2. 寫 requirements.txt(列出所有要用的套件)
3. 寫 app/main.py(FastAPI 啟動)
4. 寫 app/database.py(SQLAlchemy 連 SQLite)
5. 寫 app/models/(etf_list / daily_kbar / news 三個 model)
6. 寫一個簡單首頁 templates/index.html
   - 用 Tailwind CDN
   - 暗色系
   - 顯示「ETF Dashboard - 開發中」
   - 套上設計規範的配色
7. 寫 .env.example(空的環境變數範本)
8. 寫 README.md(怎麼啟動)
9. git init + 初始 commit

【完成標準】
- 我能執行 uvicorn app.main:app --reload
- 打開 http://localhost:8000 看到暗色系首頁
- DB 自動建立(SQLite 單檔在 data/etf.db)
- 三個 table 已建好

做完才回來,給我:
- 啟動指令
- 專案目錄樹
- 第一個 commit hash

不要每寫一個檔案就跑來問我,自己一路寫到底再回報。
```

---

## 預期 Claude Code 會做什麼

1. 讀 CLAUDE.md(吸收紀律)
2. 建資料夾結構
3. 寫所有檔案
4. 跑 pip install
5. 測啟動
6. git init + commit
7. 給你回報

預計 5-15 分鐘做完。

---

## 跑完之後

你打開瀏覽器 http://localhost:8000 看到暗色系首頁就成功。

接著就可以做 Step 2:ETF 排行榜。

那時候你貼這句:
```
Step 1 沒問題,接著做 Step 2:ETF 排行榜。
研究 FinMind 或其他 ETF 資料 API,選一個來源,
寫排程每天抓資料 + 排行榜頁面。
做完才回來。
```
