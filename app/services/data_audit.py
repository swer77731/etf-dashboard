"""資料健康管家 — 全自動健檢 + 自動修 + 人工待辦(2026-05-06 加)。

設計哲學:**「能自動修的自動修,修不了的進待辦」**
紀律 #21:「跑了」≠「跑對了」,所有同步任務必須有事後驗證。

架構
====
- `CHECKS` — list of check 設定(每個 check 是 dict)
- `run_all_checks()` — 跑全部 detect,結果寫 sync_status + history file
- `auto_fix_all(findings)` — 對能自動修的 finding 跑 fix_fn,連續 3 次失敗升級為待辦
- `get_latest()` — /admin/analytics 卡片用
- `get_finding(finding_id)` — detail page 用
- `ignore_finding(finding_id, days=7)` — 忽略 7 天
- `force_fix(finding_id)` — 待辦項目強制修

Finding ID 規則
================
`{kind}:{code or 'global'}` — 同一 ETF 同一類型 dedup,跨次健檢 ID 穩定可比對。

儲存
====
- 最新狀態 → `sync_status` 表 source='data_audit',missing_items 存 JSON list
- 歷史 → `data/audit_history/YYYY-MM-DD.log`(line-based JSON,一行一筆 finding)
- 忽略清單 → `data/audit_ignored.json`(dict: {finding_id: ignore_until_iso})
- 30 天保留:跑時順手刪超過 30 天的歷史檔
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import func, select

from app.config import DATA_DIR
from app.database import session_scope
from app.models.dividend import Dividend
from app.models.etf import ETF
from app.models.kbar import DailyKBar
from app.services.sync_status import record_sync_attempt

logger = logging.getLogger(__name__)

SYNC_SOURCE = "data_audit"
HISTORY_DIR = DATA_DIR / "audit_history"
IGNORED_FILE = DATA_DIR / "audit_ignored.json"
HISTORY_RETAIN_DAYS = 30
MAX_FIX_ATTEMPTS = 3
MAX_FIXES_PER_RUN = 10   # 一輪 audit 最多自動修 N 個(避免撞 FinMind quota)
_MAX_FINDINGS_JSON_BYTES = 1_000_000   # missing_items JSON 上限(SQLite TEXT 上限 1GB,1MB sanity)

HISTORY_DIR.mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# Finding dataclass
# ─────────────────────────────────────────────────────────────


@dataclass
class Finding:
    id: str                                # "{kind}:{code or 'global'}"
    kind: str                              # check id
    label: str                             # 中文人類可讀
    severity: str                          # "info" / "warn" / "error"
    code: str | None                       # 相關 ETF code(可空)
    detail: str                            # 一句描述
    auto_fixable: bool
    auto_fixed: bool = False
    fix_attempts: int = 0
    fix_log: list[str] = field(default_factory=list)
    ignored_until: str | None = None       # ISO date string
    created_at: str = ""                   # ISO datetime UTC
    metadata: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.ignored_until and self.ignored_until > datetime.now(timezone.utc).date().isoformat():
            return "ignored"
        if self.auto_fixed:
            return "fixed"
        if self.auto_fixable and self.fix_attempts >= MAX_FIX_ATTEMPTS:
            return "todo"
        if not self.auto_fixable:
            return "todo"
        return "pending"


def _finding_to_dict(f: Finding) -> dict:
    """asdict() 不會 include @property,手動加 status。"""
    d = asdict(f)
    d["status"] = f.status
    return d


# ─────────────────────────────────────────────────────────────
# Detect functions
# ─────────────────────────────────────────────────────────────


def _detect_kbar_adj_null() -> list[Finding]:
    """K 棒 adj_close 缺漏 — 最近 7 天 NULL ≥ 2 天的 active 非 index ETF。"""
    today = date.today()
    cutoff = today - timedelta(days=7)
    out: list[Finding] = []
    with session_scope() as s:
        rows = s.execute(
            select(ETF.code, ETF.name, func.count(DailyKBar.id))
            .join(DailyKBar, DailyKBar.etf_id == ETF.id)
            .where(DailyKBar.date >= cutoff)
            .where(DailyKBar.date < today)   # 排除「今天」(FinMind 通常隔天才釋出)
            .where(DailyKBar.adj_close.is_(None))
            .where(ETF.category != "index")
            .where(ETF.is_active.is_(True))
            .group_by(ETF.code, ETF.name)
            .having(func.count(DailyKBar.id) >= 2)
        ).all()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for code, name, null_n in rows:
        out.append(Finding(
            id=f"kbar_adj_null:{code}",
            kind="kbar_adj_null",
            label="K 棒 adj_close 缺漏",
            severity="warn",
            code=code,
            detail=f"{code} {name} 最近 7 天有 {null_n} 天 adj_close NULL",
            auto_fixable=True,
            created_at=now_iso,
            metadata={"name": name, "null_days": null_n},
        ))
    return out


def _detect_kbar_stale() -> list[Finding]:
    """ETF 7 天無新 K 棒 — active ETF 最後 K 棒日期 < today-7。"""
    today = date.today()
    threshold = today - timedelta(days=7)
    out: list[Finding] = []
    with session_scope() as s:
        rows = s.execute(
            select(ETF.code, ETF.name, ETF.category, func.max(DailyKBar.date).label("last_d"))
            .join(DailyKBar, DailyKBar.etf_id == ETF.id, isouter=True)
            .where(ETF.is_active.is_(True))
            .where(ETF.category != "index")
            .group_by(ETF.code, ETF.name, ETF.category)
        ).all()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for code, name, cat, last_d in rows:
        if last_d is None or last_d < threshold:
            days_ago = (today - last_d).days if last_d else 999
            out.append(Finding(
                id=f"kbar_stale:{code}",
                kind="kbar_stale",
                label="ETF K 棒長期未更新",
                severity="warn",
                code=code,
                detail=f"{code} {name} 最後 K 棒日期 {last_d} ({days_ago} 天前)",
                auto_fixable=True,
                created_at=now_iso,
                metadata={"name": name, "last_kbar_date": last_d.isoformat() if last_d else None,
                          "days_ago": days_ago, "category": cat},
            ))
    return out


def _detect_etf_likely_delisted() -> list[Finding]:
    """ETF 90 天無新 K 棒 + 曾經有過資料 → 疑似下市候選。

    紀律 #21:不直接自動 inactive。只進待辦清單,由 admin 點「強制修」
    觸發 _fix_etf_likely_delisted() 才實際 UPDATE is_active=False。
    這層架在 kbar_stale(>7 天)之上,用 90 天當「絕對下市」門檻。
    """
    today = date.today()
    threshold = today - timedelta(days=90)
    out: list[Finding] = []
    with session_scope() as s:
        rows = s.execute(
            select(
                ETF.code, ETF.name, ETF.category,
                func.max(DailyKBar.date).label("last_d"),
                func.count(DailyKBar.id).label("kbar_count"),
            )
            .join(DailyKBar, DailyKBar.etf_id == ETF.id, isouter=True)
            .where(ETF.is_active.is_(True))
            .where(ETF.category != "index")
            .group_by(ETF.code, ETF.name, ETF.category)
        ).all()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for code, name, cat, last_d, kbar_count in rows:
        # 條件:有過 kbar(kbar_count > 0)+ 最後 K 棒 < 90 天前
        # 沒過 kbar 的(全市場新發行 < 1 週的)不誤殺
        if kbar_count == 0 or last_d is None:
            continue
        if last_d >= threshold:
            continue
        days_ago = (today - last_d).days
        out.append(Finding(
            id=f"etf_likely_delisted:{code}",
            kind="etf_likely_delisted",
            label="ETF 疑似下市(90 天無新 K 棒)",
            severity="warn",
            code=code,
            detail=(
                f"{code} {name} 最後 K 棒 {last_d} ({days_ago} 天前)"
                f"— 強制修可自動標 inactive"
            ),
            auto_fixable=False,    # 不自動跑,需 admin 確認
            created_at=now_iso,
            metadata={
                "name": name,
                "last_kbar_date": last_d.isoformat(),
                "days_ago": days_ago,
                "category": cat,
                "kbar_count": kbar_count,
            },
        ))
    return out


def _fix_etf_likely_delisted(finding: Finding) -> tuple[bool, str]:
    """將 etf_list.is_active 設為 False。force_fix 才會跑。"""
    code = finding.code
    if not code:
        return False, "no code"
    try:
        from sqlalchemy import update as _upd
        with session_scope() as s:
            r = s.execute(
                _upd(ETF).where(ETF.code == code).values(is_active=False)
            )
            return r.rowcount > 0, f"set is_active=False (rows={r.rowcount})"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def _detect_dividend_pending_amount() -> list[Finding]:
    """配息已公告但 cash_dividend NULL 超過 30 天 — 應該重抓。"""
    today = date.today()
    cutoff = today - timedelta(days=30)
    out: list[Finding] = []
    with session_scope() as s:
        rows = s.execute(
            select(ETF.code, ETF.name, Dividend.ex_date, Dividend.announce_date)
            .join(Dividend, Dividend.etf_id == ETF.id)
            .where(Dividend.cash_dividend.is_(None))
            .where(Dividend.announce_date.is_not(None))
            .where(Dividend.announce_date < cutoff)
        ).all()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for code, name, ex_d, ann_d in rows:
        out.append(Finding(
            id=f"dividend_pending_amount:{code}:{ex_d.isoformat()}",
            kind="dividend_pending_amount",
            label="配息金額未公告超過 30 天",
            severity="warn",
            code=code,
            detail=f"{code} {name} 除息日 {ex_d}、公告日 {ann_d},配息金額仍 NULL",
            auto_fixable=True,
            created_at=now_iso,
            metadata={"name": name, "ex_date": ex_d.isoformat(),
                      "announce_date": ann_d.isoformat() if ann_d else None},
        ))
    return out


def _detect_yearly_return_outlier() -> list[Finding]:
    """年度報酬離譜 — annual_return > +500% 或 < -80% (排除當年 partial)。

    這類異常程式分不清「真的暴漲 / 真的暴跌(reverse split)/ bug」,進待辦。
    """
    out: list[Finding] = []
    with session_scope() as s:
        try:
            from app.models.etf_yearly_return import EtfYearlyReturn
            rows = s.execute(
                select(EtfYearlyReturn.etf_code, EtfYearlyReturn.year, EtfYearlyReturn.annual_return)
                .where(EtfYearlyReturn.is_partial == 0)
                .where((EtfYearlyReturn.annual_return > 5.0) | (EtfYearlyReturn.annual_return < -0.80))
            ).all()
        except Exception:
            logger.exception("[audit] yearly_return query failed")
            return []
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for code, year, ret in rows:
        out.append(Finding(
            id=f"yearly_return_outlier:{code}:{year}",
            kind="yearly_return_outlier",
            label="年度報酬離譜(可能假可能真)",
            severity="error",
            code=code,
            detail=f"{code} {year} 年度報酬 = {ret*100:+.1f}%(可能 reverse split 假象,需人工判斷)",
            auto_fixable=False,
            created_at=now_iso,
            metadata={"year": year, "annual_return": ret},
        ))
    return out


# ─────────────────────────────────────────────────────────────
# Fix functions
# ─────────────────────────────────────────────────────────────


def _fix_kbar_adj_null(finding: Finding) -> tuple[bool, str]:
    """重抓最近 14 天 raw + adj,UPSERT 蓋過 NULL adj_close。"""
    from app.services.kbar_sync import (
        _fetch_adj, _fetch_raw, _merge_raw_adj, _persist_kbars,
    )
    code = finding.code
    if not code:
        return False, "no code"
    with session_scope() as s:
        etf = s.scalar(select(ETF).where(ETF.code == code))
        if not etf:
            return False, f"ETF {code} not found"
        eid = etf.id
    end = date.today()
    start = end - timedelta(days=14)
    try:
        raw = _fetch_raw(code, start, end)
        adj = _fetch_adj(code, start, end)
        merged = _merge_raw_adj(raw, adj)
        n = _persist_kbars(eid, merged)
        return True, f"refetched {len(raw)} raw / {len(adj)} adj rows, persisted {n}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def _fix_kbar_stale(finding: Finding) -> tuple[bool, str]:
    """觸發該 ETF 增量 sync_one_etf。"""
    from app.services.kbar_sync import sync_one_etf
    code = finding.code
    if not code:
        return False, "no code"
    with session_scope() as s:
        etf = s.scalar(select(ETF).where(ETF.code == code))
        if not etf:
            return False, f"ETF {code} not found"
    try:
        result = sync_one_etf(etf)
        return True, f"sync_one_etf: rows={result['rows']} mode={result['mode']}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


def _fix_dividend_pending_amount(finding: Finding) -> tuple[bool, str]:
    """重抓 dividend_announce — TWSE 公告爬蟲。"""
    from app.services.dividend_announce_sync import sync_all
    try:
        stats = sync_all()
        return True, f"announce sync: {stats}"
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:120]}"


# ─────────────────────────────────────────────────────────────
# Check 註冊表
# ─────────────────────────────────────────────────────────────


CHECKS: list[dict] = [
    {
        "id": "kbar_adj_null",
        "label": "K 棒 adj_close 缺漏",
        "auto_fixable": True,
        "detect_fn": _detect_kbar_adj_null,
        "fix_fn": _fix_kbar_adj_null,
    },
    {
        "id": "kbar_stale",
        "label": "ETF K 棒長期未更新",
        "auto_fixable": True,
        "detect_fn": _detect_kbar_stale,
        "fix_fn": _fix_kbar_stale,
    },
    {
        # 預埋:90 天無 K 棒 → 進「人工待辦」候選下市清單
        # auto_fixable=False:不自動 inactive,需 admin 在 detail page 點「強制修復」
        # 確認後才 UPDATE。force_fix 會用 fix_fn 即使 auto_fixable=False。
        "id": "etf_likely_delisted",
        "label": "ETF 疑似下市(90 天無新 K 棒)",
        "auto_fixable": False,
        "detect_fn": _detect_etf_likely_delisted,
        "fix_fn": _fix_etf_likely_delisted,
    },
    {
        "id": "dividend_pending_amount",
        "label": "配息金額未公告超過 30 天",
        "auto_fixable": True,
        "detect_fn": _detect_dividend_pending_amount,
        "fix_fn": _fix_dividend_pending_amount,
    },
    {
        "id": "yearly_return_outlier",
        "label": "年度報酬離譜(需人工判斷)",
        "auto_fixable": False,
        "detect_fn": _detect_yearly_return_outlier,
        "fix_fn": None,
    },
]


# ─────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────


def _load_ignored() -> dict[str, str]:
    """{finding_id: ignore_until_iso_date}"""
    if not IGNORED_FILE.exists():
        return {}
    try:
        return json.loads(IGNORED_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("[audit] ignored file unreadable, treating as empty")
        return {}


def _save_ignored(d: dict[str, str]) -> None:
    IGNORED_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _apply_ignored(findings: list[Finding]) -> list[Finding]:
    """對 finding 標記 ignored_until,不過濾(讓 UI 知道有忽略項)。"""
    today_iso = date.today().isoformat()
    ignored = _load_ignored()
    # 自動清過期項
    ignored = {k: v for k, v in ignored.items() if v >= today_iso}
    _save_ignored(ignored)
    for f in findings:
        if f.id in ignored:
            f.ignored_until = ignored[f.id]
    return findings


def _findings_to_json(findings: list[Finding]) -> str:
    return json.dumps([_finding_to_dict(f) for f in findings], ensure_ascii=False)


def _findings_from_json(s: str) -> list[Finding]:
    if not s:
        return []
    try:
        data = json.loads(s)
    except Exception:
        return []
    out = []
    valid_keys = {f.name for f in Finding.__dataclass_fields__.values()}
    for d in data:
        # strip 非 dataclass field(例如 status @property 在 _finding_to_dict 加的)
        clean = {k: v for k, v in d.items() if k in valid_keys}
        out.append(Finding(**clean))
    return out


def _persist_to_sync_status(findings: list[Finding]) -> None:
    """寫進 sync_status source='data_audit',missing_items 存 JSON list。

    紀律 #16(2026-05-08 修)— 之前 cap 設 64KB 過度防呆,causes 「資料健康狀態
    全部正常」假象:當 findings 序列化超過 64KB(255 ETF × 多 check ≈ 100-200KB)
    JSON 被切壞 → `_findings_from_json` parse 失敗 → admin UI 拿到空 list →
    錯顯「全部正常」。SQLite TEXT 實際上限是 SQLITE_MAX_LENGTH(1GB),拉到
    1MB 是極寬鬆的 sanity 保護(找到極端 bug 才會撞)。
    """
    json_str = _findings_to_json(findings)
    todo_n = sum(1 for f in findings if f.status == "todo")
    fixed_n = sum(1 for f in findings if f.status == "fixed")
    capped = json_str[:_MAX_FINDINGS_JSON_BYTES]
    if len(json_str) > _MAX_FINDINGS_JSON_BYTES:
        logger.warning(
            "[audit] findings JSON %d bytes exceeds %d cap — truncated",
            len(json_str), _MAX_FINDINGS_JSON_BYTES,
        )

    # 直接走 raw SQL 因為 record_sync_attempt 不接受長 JSON
    from sqlalchemy import select as _sel

    from app.models.sync_status import SyncStatus
    with session_scope() as s:
        row = s.scalar(_sel(SyncStatus).where(SyncStatus.source == SYNC_SOURCE))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if row is None:
            row = SyncStatus(
                source=SYNC_SOURCE,
                last_attempt_at=now,
                last_success_at=now if todo_n == 0 else None,
                last_error=f"todo={todo_n}, fixed={fixed_n}" if todo_n else None,
                rows_synced=len(findings),
                retry_count=0,
                missing_count=todo_n,
                missing_items=capped,
            )
            s.add(row)
        else:
            row.last_attempt_at = now
            row.rows_synced = len(findings)
            row.missing_count = todo_n
            row.missing_items = capped
            if todo_n == 0:
                row.last_success_at = now
                row.last_error = None
            else:
                row.last_error = f"todo={todo_n}, fixed={fixed_n}"


def _append_history(findings: list[Finding]) -> None:
    """每天一個檔案,line-based JSON。30 天保留。"""
    today = date.today()
    f_path = HISTORY_DIR / f"{today.isoformat()}.log"
    line = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total": len(findings),
        "fixed": sum(1 for f in findings if f.status == "fixed"),
        "todo": sum(1 for f in findings if f.status == "todo"),
        "ignored": sum(1 for f in findings if f.status == "ignored"),
        "findings": [_finding_to_dict(f) for f in findings],
    }, ensure_ascii=False)
    with f_path.open("a", encoding="utf-8") as fp:
        fp.write(line + "\n")
    # cleanup 30 天前的歷史檔
    cutoff = today - timedelta(days=HISTORY_RETAIN_DAYS)
    for old in HISTORY_DIR.glob("*.log"):
        try:
            d = date.fromisoformat(old.stem)
            if d < cutoff:
                old.unlink()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────────────────────────


def run_all_checks(auto_fix: bool = True) -> dict:
    """跑全部 checks → 自動修 → 寫 sync_status + history。

    回:{total, fixed, todo, ignored, findings}
    """
    started_at = time.time()
    all_findings: list[Finding] = []
    for chk in CHECKS:
        try:
            results = chk["detect_fn"]()
            logger.info("[audit] check %s detected %d findings", chk["id"], len(results))
            all_findings.extend(results)
        except Exception:
            logger.exception("[audit] check %s detect_fn failed", chk["id"])

    # 套忽略清單
    all_findings = _apply_ignored(all_findings)

    # 自動修(只對 status='pending' 的,單輪上限 MAX_FIXES_PER_RUN 避免撞 quota)
    # 設計:fix_fn 回 ok=True 就信任,不立刻 re-detect 驗證(省 quota)
    # 真的有沒修好下次 cron 自然會再 detect 抓到。failed 一次就 +1 attempt;
    # 累積 attempts >= MAX_FIX_ATTEMPTS → status 自動變 'todo'(可看 finding.status)
    if auto_fix:
        fixed_count = 0
        for finding in all_findings:
            if fixed_count >= MAX_FIXES_PER_RUN:
                finding.fix_log.append(
                    f"skipped: 本輪已修 {MAX_FIXES_PER_RUN} 個,留待下次"
                )
                continue
            if finding.status != "pending":
                continue
            chk = next((c for c in CHECKS if c["id"] == finding.kind), None)
            if not chk or not chk["fix_fn"]:
                continue
            finding.fix_attempts += 1
            try:
                ok, msg = chk["fix_fn"](finding)
            except Exception as e:
                ok, msg = False, f"{type(e).__name__}: {str(e)[:120]}"
            finding.fix_log.append(f"attempt {finding.fix_attempts}: {'OK' if ok else 'FAIL'} - {msg}")
            if ok:
                finding.auto_fixed = True
                fixed_count += 1

    _persist_to_sync_status(all_findings)
    _append_history(all_findings)

    elapsed = time.time() - started_at
    summary = {
        "elapsed_sec": round(elapsed, 2),
        "total": len(all_findings),
        "fixed": sum(1 for f in all_findings if f.status == "fixed"),
        "todo": sum(1 for f in all_findings if f.status == "todo"),
        "ignored": sum(1 for f in all_findings if f.status == "ignored"),
    }
    logger.info("[audit] done — %s", summary)
    return {**summary, "findings": [_finding_to_dict(f) for f in all_findings]}


def get_latest() -> dict:
    """讀 sync_status + 解析,給 /admin/analytics 卡片用。"""
    from sqlalchemy import select as _sel

    from app.models.sync_status import SyncStatus
    with session_scope() as s:
        row = s.scalar(_sel(SyncStatus).where(SyncStatus.source == SYNC_SOURCE))
        if not row:
            return {
                "exists": False,
                "last_run_at": None,
                "total": 0,
                "fixed": 0,
                "todo": 0,
                "ignored": 0,
                "findings": [],
            }
        findings = _findings_from_json(row.missing_items or "")
        return {
            "exists": True,
            "last_run_at": row.last_attempt_at.isoformat(timespec="seconds") if row.last_attempt_at else None,
            "total": len(findings),
            "fixed": sum(1 for f in findings if f.status == "fixed"),
            "todo": sum(1 for f in findings if f.status == "todo"),
            "ignored": sum(1 for f in findings if f.status == "ignored"),
            "findings": [_finding_to_dict(f) for f in findings],
        }


def get_finding(finding_id: str) -> dict | None:
    """給 detail page 用。"""
    latest = get_latest()
    for f in latest["findings"]:
        if f["id"] == finding_id:
            return f
    return None


def ignore_finding(finding_id: str, days: int = 7) -> bool:
    until = (date.today() + timedelta(days=days)).isoformat()
    ignored = _load_ignored()
    ignored[finding_id] = until
    _save_ignored(ignored)
    logger.info("[audit] ignore %s until %s", finding_id, until)
    # 同步更新 sync_status missing_items 內該 finding 的 ignored_until
    latest = get_latest()
    findings = [Finding(**f) for f in latest["findings"]]
    for f in findings:
        if f.id == finding_id:
            f.ignored_until = until
    _persist_to_sync_status(findings)
    return True


def force_fix(finding_id: str) -> tuple[bool, str]:
    """強制嘗試修一次(不管之前是否 todo)。回 (ok, msg)。"""
    latest = get_latest()
    target = next((f for f in latest["findings"] if f["id"] == finding_id), None)
    if not target:
        return False, "finding not found"
    chk = next((c for c in CHECKS if c["id"] == target["kind"]), None)
    if not chk or not chk["fix_fn"]:
        return False, "no fix_fn for this kind"
    finding = Finding(**target)
    finding.fix_attempts += 1
    try:
        ok, msg = chk["fix_fn"](finding)
    except Exception as e:
        ok, msg = False, f"{type(e).__name__}: {str(e)[:120]}"
    finding.fix_log.append(f"force_fix: {'OK' if ok else 'FAIL'} - {msg}")
    if ok:
        re_results = chk["detect_fn"]()
        if not any(r.id == finding.id for r in re_results):
            finding.auto_fixed = True
            finding.fix_log.append("verified: 強制修復後重檢通過")

    # 更新 sync_status
    findings = [Finding(**f) for f in latest["findings"]]
    for i, f in enumerate(findings):
        if f.id == finding_id:
            findings[i] = finding
    _persist_to_sync_status(findings)
    return ok, msg
