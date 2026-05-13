"""教學專區 — /learn CMS routes(列表/文章/新增/編輯/儲存/刪除/發布/撤回)。

紀律:
- admin only 動作走 _require_admin(Google OAuth + ADMIN_EMAILS)
- Markdown render server side(防 client hack)
- access_level: public / login / sponsor
- sponsor 暫不啟用 → 顯示佔位頁
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select

from app.config import PROJECT_ROOT
from app.database import session_scope
from app.models.learn import LearnArticle, LearnCategory
from app.services.learn_md import (
    ensure_unique_slug,
    render_markdown,
    title_to_slug,
    validate_slug,
)

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory=str(PROJECT_ROOT / "templates"))

# ─── admin 檢查(局部 import 避免 circular) ───
def _require_admin(request: Request):
    from app.routers.admin import _require_admin as _ra
    return _ra(request)


def _is_admin(request: Request) -> bool:
    from app.routers.admin import _is_site_admin
    return _is_site_admin(request)[0]


def _common(request: Request) -> dict:
    """共用 ctx(brand 等 + is_admin flag)。"""
    from app.routers.pages import _common_ctx
    ctx = _common_ctx(request)
    ctx["is_admin"] = _is_admin(request)
    return ctx


# ─────────────────────────────────────────────────────────────
# 公開 routes
# ─────────────────────────────────────────────────────────────
@router.get("/learn", response_class=HTMLResponse)
async def learn_list(request: Request, category: str | None = None, page: int = 1):
    """文章列表 + 分類 tab + 分頁。"""
    page = max(1, page)
    per_page = 12

    with session_scope() as session:
        # 抓所有分類 + 每類已發布文章數
        all_cats = list(session.scalars(
            select(LearnCategory).order_by(LearnCategory.display_order)
        ))
        cat_counts: dict[int, int] = {}
        for c in all_cats:
            cnt = session.scalar(
                select(func.count()).select_from(LearnArticle).where(
                    LearnArticle.category_id == c.id,
                    LearnArticle.status == "published",
                    LearnArticle.deleted_at.is_(None),
                )
            ) or 0
            cat_counts[c.id] = cnt
        # 動態 tab:只顯示 count > 0 的(reserved 永遠不顯示)
        tab_cats = [c for c in all_cats if cat_counts.get(c.id, 0) > 0 and c.slug != "reserved"]
        total_pub = sum(cat_counts.values())

        # 查當前 filter 後的 articles
        q = select(LearnArticle).where(
            LearnArticle.status == "published",
            LearnArticle.deleted_at.is_(None),
        )
        cur_cat = None
        if category:
            cur_cat = session.scalar(
                select(LearnCategory).where(LearnCategory.slug == category)
            )
            if cur_cat:
                q = q.where(LearnArticle.category_id == cur_cat.id)
        q = q.order_by(desc(LearnArticle.published_at))
        total_filtered = session.scalar(
            select(func.count()).select_from(q.subquery())
        ) or 0
        articles = list(session.scalars(
            q.offset((page - 1) * per_page).limit(per_page)
        ))
        # 預載 category(避免 lazy load 後 session 關掉)
        for a in articles:
            _ = a.category.name  # touch
        cat_id_to_obj = {c.id: c for c in all_cats}
        # detach
        article_views = [
            {
                "id": a.id,
                "slug": a.slug,
                "title": a.title,
                "summary": a.summary,
                "category": cat_id_to_obj.get(a.category_id),
                "access_level": a.access_level,
                "access_label": a.access_label,
                "access_color_class": a.access_color_class,
                "is_new": a.is_new,
                "reading_minutes": a.reading_minutes,
                "published_at": a.published_at,
            }
            for a in articles
        ]
        tab_data = [
            {"slug": c.slug, "name": c.name, "color": c.color, "count": cat_counts[c.id]}
            for c in tab_cats
        ]

    total_pages = max(1, (total_filtered + per_page - 1) // per_page)
    return templates.TemplateResponse(
        request, "learn/list.html",
        {
            **_common(request),
            "articles": article_views,
            "tab_cats": tab_data,
            "total_count": total_pub,
            "current_category": category,
            "page": page,
            "total_pages": total_pages,
        },
    )


@router.get("/learn/new", response_class=HTMLResponse)
async def learn_new(request: Request):
    """新增文章(admin only)。"""
    redirect = _require_admin(request)
    if redirect is not None:
        return redirect
    with session_scope() as session:
        cats = list(session.scalars(
            select(LearnCategory)
            .where(LearnCategory.slug != "reserved")
            .order_by(LearnCategory.display_order)
        ))
        cat_data = [{"id": c.id, "slug": c.slug, "name": c.name} for c in cats]
    return templates.TemplateResponse(
        request, "learn/editor.html",
        {
            **_common(request),
            "article": None,
            "cats": cat_data,
            "mode": "new",
        },
    )


@router.post("/learn/save")
async def learn_save(
    request: Request,
    title: str = Form(...),
    slug: str = Form(""),
    summary: str = Form(""),
    category_id: int = Form(...),
    access_level: str = Form("public"),
    content_md: str = Form(""),
    article_id: Optional[int] = Form(None),
    publish: str = Form(""),  # "1" 表示 publish
):
    """儲存(建立 or 更新)— admin only。"""
    redirect = _require_admin(request)
    if redirect is not None:
        return redirect

    title = title.strip()[:120]
    summary = summary.strip()[:240]
    content_md = content_md[:102400]  # 100 KB cap
    if access_level not in ("public", "login", "sponsor"):
        access_level = "public"

    # slug:auto 或 user 給的
    if not slug or not validate_slug(slug):
        slug = title_to_slug(title)

    with session_scope() as session:
        # 驗 category
        cat = session.scalar(select(LearnCategory).where(LearnCategory.id == category_id))
        if not cat:
            raise HTTPException(400, "category 不存在")

        if article_id:
            article = session.scalar(select(LearnArticle).where(LearnArticle.id == article_id))
            if not article or article.deleted_at:
                raise HTTPException(404, "文章不存在")
            # slug 唯一(排除自己)
            article.slug = ensure_unique_slug(session, slug, exclude_id=article.id)
            article.title = title
            article.summary = summary
            article.category_id = category_id
            article.access_level = access_level
            article.content_md = content_md
            article.content_html = render_markdown(content_md)
            if publish == "1" and article.status != "published":
                article.status = "published"
                article.published_at = datetime.now()
        else:
            unique_slug = ensure_unique_slug(session, slug)
            article = LearnArticle(
                slug=unique_slug,
                title=title,
                summary=summary,
                content_md=content_md,
                content_html=render_markdown(content_md),
                category_id=category_id,
                access_level=access_level,
                status="published" if publish == "1" else "draft",
            )
            if publish == "1":
                article.published_at = datetime.now()
            session.add(article)
            session.flush()  # 拿 id

        target_slug = article.slug

    return RedirectResponse(url=f"/learn/{target_slug}", status_code=303)


@router.get("/learn/edit/{article_id}", response_class=HTMLResponse)
async def learn_edit(request: Request, article_id: int):
    """編輯文章 — admin only。"""
    redirect = _require_admin(request)
    if redirect is not None:
        return redirect
    with session_scope() as session:
        article = session.scalar(select(LearnArticle).where(LearnArticle.id == article_id))
        if not article or article.deleted_at:
            raise HTTPException(404, "文章不存在")
        cats = list(session.scalars(
            select(LearnCategory)
            .where(LearnCategory.slug != "reserved")
            .order_by(LearnCategory.display_order)
        ))
        cat_data = [{"id": c.id, "slug": c.slug, "name": c.name} for c in cats]
        article_data = {
            "id": article.id,
            "slug": article.slug,
            "title": article.title,
            "summary": article.summary,
            "category_id": article.category_id,
            "access_level": article.access_level,
            "content_md": article.content_md,
            "status": article.status,
        }
    return templates.TemplateResponse(
        request, "learn/editor.html",
        {
            **_common(request),
            "article": article_data,
            "cats": cat_data,
            "mode": "edit",
        },
    )


@router.post("/learn/delete/{article_id}")
async def learn_delete(request: Request, article_id: int):
    """軟刪除 — admin only。"""
    redirect = _require_admin(request)
    if redirect is not None:
        return redirect
    with session_scope() as session:
        article = session.scalar(select(LearnArticle).where(LearnArticle.id == article_id))
        if not article:
            raise HTTPException(404)
        article.deleted_at = datetime.now()
    return RedirectResponse(url="/learn", status_code=303)


@router.post("/learn/publish/{article_id}")
async def learn_publish(request: Request, article_id: int):
    """發布 — admin only。"""
    redirect = _require_admin(request)
    if redirect is not None:
        return redirect
    with session_scope() as session:
        article = session.scalar(select(LearnArticle).where(LearnArticle.id == article_id))
        if not article or article.deleted_at:
            raise HTTPException(404)
        if article.status != "published":
            article.status = "published"
            article.published_at = datetime.now()
        target_slug = article.slug
    return RedirectResponse(url=f"/learn/{target_slug}", status_code=303)


@router.post("/learn/unpublish/{article_id}")
async def learn_unpublish(request: Request, article_id: int):
    """撤回 — admin only。"""
    redirect = _require_admin(request)
    if redirect is not None:
        return redirect
    with session_scope() as session:
        article = session.scalar(select(LearnArticle).where(LearnArticle.id == article_id))
        if not article or article.deleted_at:
            raise HTTPException(404)
        article.status = "draft"
    return RedirectResponse(url="/learn", status_code=303)


@router.get("/learn/{slug}", response_class=HTMLResponse)
async def learn_article(request: Request, slug: str):
    """單篇文章頁(public)。access_level 控制可見性。"""
    if not validate_slug(slug):
        raise HTTPException(404)

    with session_scope() as session:
        article = session.scalar(
            select(LearnArticle).where(
                LearnArticle.slug == slug,
                LearnArticle.deleted_at.is_(None),
            )
        )
        if not article:
            raise HTTPException(404, "文章不存在")
        # draft 只 admin 看得到
        if article.status != "published" and not _is_admin(request):
            raise HTTPException(404, "文章未發布")

        # access_level
        user = getattr(request.state, "user", None)
        if article.access_level == "login" and not user and not _is_admin(request):
            return RedirectResponse(url=f"/auth/google/login?next=/learn/{slug}", status_code=302)
        is_sponsor_block = (article.access_level == "sponsor")

        # 找同分類前/後/延伸閱讀
        same_cat_q = select(LearnArticle).where(
            LearnArticle.category_id == article.category_id,
            LearnArticle.status == "published",
            LearnArticle.deleted_at.is_(None),
            LearnArticle.id != article.id,
        ).order_by(desc(LearnArticle.published_at))
        related = list(session.scalars(same_cat_q.limit(3)))

        # view count + 1
        article.view_count = (article.view_count or 0) + 1
        category = article.category
        # detach
        article_view = {
            "id": article.id,
            "slug": article.slug,
            "title": article.title,
            "summary": article.summary,
            "content_html": article.content_html,
            "category": {"slug": category.slug, "name": category.name, "color": category.color},
            "access_level": article.access_level,
            "access_label": article.access_label,
            "access_color_class": article.access_color_class,
            "is_new": article.is_new,
            "reading_minutes": article.reading_minutes,
            "author": article.author,
            "published_at": article.published_at,
            "is_sponsor_block": is_sponsor_block,
        }
        related_views = [
            {"slug": r.slug, "title": r.title, "summary": r.summary, "is_new": r.is_new}
            for r in related
        ]

    return templates.TemplateResponse(
        request, "learn/article.html",
        {
            **_common(request),
            "article": article_view,
            "related": related_views,
        },
    )
