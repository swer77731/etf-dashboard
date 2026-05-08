"""One-shot DB migrations(SQLite,沒用 alembic)。

每個檔案 NNN_描述.py,內含 `run(dry_run=True)` 函式。
獨立執行:`python -m app.migrations.001_dividend_nullable_amounts [--apply]`
"""
