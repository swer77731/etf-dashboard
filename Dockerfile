FROM python:3.12-slim

WORKDIR /app

# 系統依賴(部分 wheel 需要 gcc;之後若加 lxml/cffi 等可能還需更多 -dev)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 先複製 requirements.txt 利用 Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製整個專案
COPY . .

# 建立 data 目錄(Zeabur Volume 會掛在這個位置持久化 SQLite)
RUN mkdir -p /app/data

# Zeabur 會給 PORT 環境變數,fallback 8000
EXPOSE 8000

# 啟動指令:用 sh 才能展開 ${PORT}
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
