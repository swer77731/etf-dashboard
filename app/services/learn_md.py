"""Markdown 渲染 + slug 工具(教學專區用)。

- markdown-it-py:tables / strikethrough / autolink,關閉 raw HTML(防 XSS)
- pygments:code block 語法上色
- pypinyin:中文標題轉拼音當 slug fallback
"""
from __future__ import annotations

import re
import unicodedata

from markdown_it import MarkdownIt
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.util import ClassNotFound

# Markdown-it 設定:turn on tables / strikethrough、html=False 防 XSS
# linkify 需 linkify-it-py 套件,不裝,連結用顯式 [text](url) 語法
_md = (
    MarkdownIt("default", {"html": False, "typographer": True})
    .enable(["table", "strikethrough"])
)


def _highlight_code(code: str, lang: str | None, attrs: str) -> str:
    """Pygments 上色;未知 lang fallback plain text。"""
    try:
        lexer = get_lexer_by_name(lang) if lang else TextLexer()
    except ClassNotFound:
        lexer = TextLexer()
    formatter = HtmlFormatter(nowrap=True, cssclass="codehilite")
    return f'<pre class="codehilite"><code class="language-{lang or "text"}">{highlight(code, lexer, formatter).strip()}</code></pre>'


# Hook pygments 進 fence renderer
_md.options["highlight"] = _highlight_code


def render_markdown(md_text: str) -> str:
    """安全 render markdown → html。"""
    if not md_text:
        return ""
    return _md.render(md_text)


# ─────────────────────────────────────────────────────────────
# Slug 生成
# ─────────────────────────────────────────────────────────────
_SLUG_INVALID_RE = re.compile(r"[^a-z0-9-]+")
_SLUG_DASH_RE = re.compile(r"-+")


def title_to_slug(title: str) -> str:
    """中文標題 → 拼音 slug(a-z, 0-9, -)。"""
    if not title:
        return "untitled"
    try:
        from pypinyin import lazy_pinyin
        py = lazy_pinyin(title)
        text = "-".join(py).lower()
    except Exception:
        text = title.lower()
    # 純化:Unicode NFKD + 過濾合法字元
    text = unicodedata.normalize("NFKD", text)
    text = _SLUG_INVALID_RE.sub("-", text)
    text = _SLUG_DASH_RE.sub("-", text).strip("-")
    return text[:80] or "untitled"


def ensure_unique_slug(session, base_slug: str, exclude_id: int | None = None) -> str:
    """若 slug 重複,加 -2, -3 ... 後綴直到唯一。"""
    from sqlalchemy import select
    from app.models.learn import LearnArticle

    candidate = base_slug
    suffix = 2
    while True:
        q = select(LearnArticle.id).where(LearnArticle.slug == candidate)
        if exclude_id is not None:
            q = q.where(LearnArticle.id != exclude_id)
        existing = session.scalar(q)
        if not existing:
            return candidate
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
        if suffix > 100:
            # safety:加 timestamp 防無限
            from datetime import datetime
            return f"{base_slug}-{int(datetime.now().timestamp())}"


def validate_slug(slug: str) -> bool:
    """slug 只允許 a-z 0-9 -(防 path injection)。"""
    return bool(re.fullmatch(r"[a-z0-9-]+", slug or ""))
