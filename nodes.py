"""
All state, config, LLM, and graph nodes in one file (dev-simple).
Uses structured output (Pydantic) and state-based max_tokens instead of manual string cutting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import operator
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field
from tavily import TavilyClient
from tenacity import retry, stop_after_attempt, wait_exponential
from typing_extensions import Annotated, NotRequired, Required, TypedDict

load_dotenv()

logger = logging.getLogger("blog_writer_simple")


# ---------------------------------------------------------------------------
# Token budget from state (words ~= tokens / 1.3 for English; use 1.5x for safety)
# ---------------------------------------------------------------------------

def words_to_max_tokens(words: int) -> int:
    """Approx max tokens so LLM output stays near word budget. No manual string cutting."""
    return max(100, int(words * 1.5))


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class BlogState(TypedDict, total=False):
    topic: Required[str]
    approx_max_words: Required[int]
    recipient_email: Required[List[str]]
    business_context: NotRequired[Optional[str]]
    location: NotRequired[Optional[str]]
    goal: NotRequired[Optional[str]]
    tone: NotRequired[Optional[str]]
    intent: NotRequired[str]
    needs_local_seo: NotRequired[bool]
    word_budget: NotRequired[Dict[str, int]]
    research_required: NotRequired[bool]
    outline: NotRequired[List[Dict[str, Any]]]
    research_data: NotRequired[Optional[str]]
    sections: NotRequired[Annotated[List[str], operator.add]]
    blog_markdown: NotRequired[str]
    final_blog: NotRequired[str]
    meta_title: NotRequired[str]
    meta_description: NotRequired[str]
    slug: NotRequired[str]
    word_count: NotRequired[int]
    quality_score: NotRequired[float]
    email_subject: NotRequired[str]
    email_html: NotRequired[str]


# ---------------------------------------------------------------------------
# Structured output schemas (Pydantic) – no manual JSON parse or string slicing
# ---------------------------------------------------------------------------

class OutlineItem(BaseModel):
    kind: str  # introduction | section | faq | conclusion | cta
    heading: str
    target_words: int
    bullets: List[str] = Field(default_factory=list)


class Outline(BaseModel):
    items: List[OutlineItem]


class ResearchSummary(BaseModel):
    facts: List[str] = Field(description="6-10 bullet facts from research")
    keywords: List[str] = Field(description="4-6 SEO keyword phrases")
    faqs: List[str] = Field(description="6 FAQ questions")
    suggested_headings: List[str] = Field(description="3 suggested section headings")
    sources: List[str] = Field(description="URLs only")


class SEOOutput(BaseModel):
    meta_title: str = Field(max_length=80)
    meta_description: str = Field(max_length=220)
    slug: str = Field(description="URL-safe slug")
    optimized_markdown: str = Field(description="Full blog markdown, same length as input or shorter")


class QualityScore(BaseModel):
    score: float = Field(ge=1, le=10, description="Quality 1-10")


class SubjectLine(BaseModel):
    subject: str = Field(max_length=90, description="Email subject line")


# ---------------------------------------------------------------------------
# Config & LLM
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    tavily_api_key: Optional[str]
    resend_api_key: Optional[str]
    resend_from_email: str
    quality_threshold: float
    log_level: str


def get_settings() -> Settings:
    openai_api_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()
    tavily_api_key = os.getenv("TAVILY_API_KEY", "").strip() or None
    resend_api_key = os.getenv("RESEND_API_KEY", "").strip() or None
    resend_from_email = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev").strip()
    quality_threshold = float(os.getenv("BLOG_QUALITY_THRESHOLD", "7").strip())
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    if not openai_api_key:
        raise RuntimeError("Missing OPENAI_API_KEY. Set it in .env or environment.")
    return Settings(
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        tavily_api_key=tavily_api_key,
        resend_api_key=resend_api_key,
        resend_from_email=resend_from_email,
        quality_threshold=quality_threshold,
        log_level=log_level,
    )


@lru_cache(maxsize=2)
def _get_writer_llm() -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(api_key=s.openai_api_key, model=s.openai_model, temperature=0.7)


@lru_cache(maxsize=2)
def _get_editor_llm() -> ChatOpenAI:
    s = get_settings()
    return ChatOpenAI(api_key=s.openai_api_key, model=s.openai_model, temperature=0.2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_str(x: Optional[str]) -> str:
    return (x or "").strip()


def _goal_normalize(goal: Optional[str]) -> str:
    g = _safe_str(goal).lower()
    return g if g in ("traffic", "leads", "authority") else "traffic"


def count_words(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or "", re.UNICODE))


def slugify(text: str) -> str:
    s = (text or "").strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"[\s_-]+", "-", s, flags=re.UNICODE)
    s = re.sub(r"^-+|-+$", "", s)
    return s or "blog"


def _highlight_code_with_pygments(code: str, lang: Optional[str] = None) -> str:
    """Highlight code block with Pygments (IDE-style syntax colors). Returns HTML with span classes."""
    try:
        from pygments import highlight
        from pygments.lexers import get_lexer_by_name, guess_lexer
        from pygments.formatters import HtmlFormatter
        from pygments.util import ClassNotFound

        code = code.strip()
        if not code:
            return '<div class="highlight"><pre><code></code></pre></div>'
        if lang:
            try:
                lexer = get_lexer_by_name(lang, stripall=False)
            except ClassNotFound:
                lexer = guess_lexer(code)
        else:
            lexer = guess_lexer(code)
        formatter = HtmlFormatter(
            cssclass="highlight",
            style="default",
            noclasses=False,
        )
        return highlight(code, lexer, formatter)
    except Exception:
        return f"<pre><code>{html_escape(code)}</code></pre>"


def _apply_syntax_highlighting(html: str) -> str:
    """Find <pre><code> blocks in HTML and replace with Pygments-highlighted HTML."""
    import re
    from html import unescape

    # Match <pre><code class="language-xxx">...</code></pre> or <pre><code>...</code></pre>
    pattern = re.compile(
        r'<pre>\s*<code(?:\s+class="[^"]*language-([a-z0-9+-]+)[^"]*")?>([\s\S]*?)</code>\s*</pre>',
        re.IGNORECASE,
    )

    def repl(match: re.Match) -> str:
        lang, code = match.group(1), match.group(2)
        code_unescaped = unescape(code).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
        return _highlight_code_with_pygments(code_unescaped, lang.strip() if lang else None)

    return pattern.sub(repl, html)


def markdown_to_html(markdown_text: str) -> str:
    try:
        import markdown as md
        import bleach

        # Convert markdown to HTML with fenced code support
        html = md.markdown(
            markdown_text or "",
            extensions=[
                "extra",
                "sane_lists",
                "fenced_code",
                "tables",
            ],
            output_format="html5",
        )

        # Apply IDE-style syntax highlighting to code blocks
        html = _apply_syntax_highlighting(html)

        allowed_tags = [
            "a", "p", "div", "span", "br", "hr",
            "ul", "ol", "li",
            "strong", "em", "blockquote",
            "code", "pre",
            "h1", "h2", "h3", "h4",
            "table", "thead", "tbody", "tr", "th", "td",
        ]

        allowed_attributes = {
            "a": ["href", "title", "rel"],
            "code": ["class"],
            "span": ["class"],
            "div": ["class"],
            "pre": ["class"],
        }

        allowed_protocols = ["http", "https", "mailto"]

        clean_html = bleach.clean(
            html,
            tags=allowed_tags,
            attributes=allowed_attributes,
            protocols=allowed_protocols,
            strip=True,
        )

        return clean_html

    except Exception:
        from html import escape
        return f"<pre>{escape(markdown_text or '')}</pre>"


def html_escape(t: str) -> str:
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _bleach_safe(text: str) -> str:
    return html_escape(text or "")


def _inline_styles_for_email(body_html: str) -> str:
    """Inject inline styles so email clients that strip <style> still show white body + cream code."""
    import re
    cream = "#faf8f5"
    # Code block: div.highlight and its inner pre (Pygments: <div class="highlight"><pre><code>...)
    body_html = re.sub(
        r'<div class="highlight">\s*<pre>',
        f'<div class="highlight" style="margin:1.5em 0;border-radius:4px;overflow:auto;background:{cream};font-size:0.875rem;line-height:1.5;">'
        f'<pre style="margin:0;padding:16px 20px;overflow-x:auto;background:{cream};border:none;color:#000;">',
        body_html,
        flags=re.IGNORECASE,
    )
    # Inline code: <code> that is not already inside a styled pre (add cream)
    body_html = re.sub(
        r'<code>',
        f'<code style="background:{cream};padding:2px 6px;border-radius:3px;color:#000;">',
        body_html,
        count=0,
    )
    # Remove inline style from <code> inside .highlight pre so we don't double-style
    body_html = re.sub(
        r'(<pre style="[^"]*">)<code style="[^"]*">',
        r'\1<code style="background:none;padding:0;">',
        body_html,
    )
    return body_html


def _pygments_css() -> str:
    """Return syntax highlighting CSS for Pygments (light background), scoped under .blog-body."""
    try:
        from pygments.formatters import HtmlFormatter
        formatter = HtmlFormatter(style="default", cssclass="highlight")
        return formatter.get_style_defs(".blog-body ")
    except Exception:
        return ""


def _medium_style_html(title: str, description: Optional[str], body_html: str) -> str:
    """Build email HTML: white background, black text, creamy code blocks, IDE-style monospace + syntax colors."""
    font_stack = '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif'
    code_font = '"Fira Code", "Cascadia Code", "Source Code Pro", Menlo, Consolas, "Liberation Mono", monospace'
    pygments_css = _pygments_css()
    style = f"""
        .blog-page {{ margin: 0; padding: 0; background: #fff; font-family: {font_stack}; color: #000; }}
        .blog-wrap {{ max-width: 680px; margin: 0 auto; padding: 40px 24px; }}
        .blog-card {{ background: #fff; padding: 48px 40px; border-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,.06); }}
        .blog-title {{ font-size: 2rem; font-weight: 700; color: #000; margin: 0 0 12px 0; letter-spacing: -0.02em; line-height: 1.2; font-family: {font_stack}; }}
        .blog-desc {{ font-size: 1rem; color: #6b6b6b; line-height: 1.6; margin: 0 0 32px 0; font-family: {font_stack}; }}
        .blog-body {{ font-size: 1.125rem; line-height: 1.7; background: #fff; color: #000; font-family: {font_stack}; }}
        .blog-body p {{ margin: 0 0 1.4em 0; }}
        .blog-body h1, .blog-body h2, .blog-body h3, .blog-body h4 {{ font-family: {font_stack}; color: #000; }}
        .blog-body h1 {{ font-size: 1.75rem; font-weight: 700; margin: 2em 0 0.6em 0; letter-spacing: -0.02em; }}
        .blog-body h2 {{ font-size: 1.5rem; font-weight: 700; margin: 2em 0 0.6em 0; letter-spacing: -0.01em; }}
        .blog-body h3 {{ font-size: 1.25rem; font-weight: 700; margin: 1.75em 0 0.5em 0; }}
        .blog-body h4 {{ font-size: 1.1rem; font-weight: 700; margin: 1.5em 0 0.5em 0; }}
        .blog-body .highlight {{ margin: 1.5em 0; border-radius: 4px; overflow: hidden; font-family: {code_font}; font-size: 0.875rem; line-height: 1.5; background: #faf8f5; }}
        .blog-body .highlight pre {{ margin: 0; padding: 16px 20px; overflow-x: auto; background: #faf8f5; border: none; font-family: {code_font}; color: #000; }}
        .blog-body code {{ background: #faf8f5; padding: 2px 6px; border-radius: 3px; font-family: {code_font}; font-size: 0.9em; color: #000; }}
        .blog-body .highlight code {{ background: none; padding: 0; font-family: {code_font}; color: inherit; }}
        .blog-body blockquote {{ margin: 1.5em 0; padding-left: 20px; border-left: 4px solid #e0e0e0; color: #555; font-style: normal; }}
        .blog-body ul, .blog-body ol {{ margin: 1em 0; padding-left: 1.5em; }}
        .blog-body li {{ margin: 0.4em 0; }}
        .blog-body a {{ color: #000; text-decoration: underline; text-underline-offset: 2px; }}
        .blog-body hr {{ border: none; border-top: 1px solid #e0e0e0; margin: 2em 0; }}
        .blog-body table {{ border-collapse: collapse; width: 100%; margin: 1.5em 0; }}
        .blog-body th, .blog-body td {{ border: 1px solid #e0e0e0; padding: 10px 12px; text-align: left; }}
        .blog-body th {{ background: #f7f7f7; font-weight: 600; }}
        .blog-body .highlight, .blog-body .highlight pre {{ background: #faf8f5; }}
        .blog-body code {{ background: #faf8f5; }}
          {pygments_css}
    """
    desc_block = f'<p class="blog-desc" style="font-size:1rem;color:#6b6b6b;line-height:1.6;margin:0 0 32px 0;">{description}</p>' if description else ""

    html_response = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <style>{style}</style>
</head>
<body class="blog-page" style="margin:0;padding:0;background:#fff;color:#000;">
  <div class="blog-wrap" style="max-width:680px;margin:0 auto;padding:40px 24px;">
    <article class="blog-card" style="background:#fff;padding:48px 40px;border-radius:4px;">
      <h1 class="blog-title" style="font-size:2rem;font-weight:700;color:#000;margin:0 0 12px 0;">{title}</h1>
      {desc_block}
      <div class="blog-body" style="font-size:1.125rem;line-height:1.7;background:#fff;color:#000;">{body_html}</div>
    </article>
  </div>
</body>
</html>"""

    print(html_response)

    return html_response


LAST_EMAIL_RESULT: Optional[Dict[str, Any]] = None


def get_last_email_result() -> Optional[Dict[str, Any]]:
    return LAST_EMAIL_RESULT


# ---------------------------------------------------------------------------
# Node: ContextAnalyzer
# ---------------------------------------------------------------------------

def _infer_intent(topic: str, business_context: Optional[str], goal: Optional[str]) -> str:
    g = _goal_normalize(goal)
    t = topic.lower()
    if business_context and g in ("leads", "traffic"):
        return "commercial_investigation"
    if any(k in t for k in ["how to", "guide", "tutorial", "what is", "examples"]):
        return "informational"
    if any(k in t for k in ["best", "top", "compare", "vs", "review"]):
        return "commercial_investigation"
    if g == "authority":
        return "informational_authority"
    return "informational"


def _detect_local_seo(topic: str, location: Optional[str]) -> bool:
    if _safe_str(location):
        return True
    t = topic.lower()
    return any(k in t for k in ["near me", "in my area", "local", "nearby"])


async def context_analyzer(state: BlogState) -> BlogState:
    topic = state["topic"].strip()
    intent = _infer_intent(topic, state.get("business_context"), state.get("goal"))
    needs_local = _detect_local_seo(topic, state.get("location"))
    logger.info("ContextAnalyzer", extra={"intent": intent, "needs_local_seo": needs_local})
    return {"intent": intent, "needs_local_seo": needs_local}


# ---------------------------------------------------------------------------
# Node: WordBudgetAllocator
# ---------------------------------------------------------------------------

async def word_budget_allocator(state: BlogState) -> BlogState:
    total = int(state["approx_max_words"])
    has_business = bool(_safe_str(state.get("business_context")))
    intro = max(90, int(total * 0.10))
    faq = max(160, int(total * 0.12))
    conclusion = max(90, int(total * 0.08))
    cta = max(90, int(total * 0.07)) if has_business else 0
    remaining = max(200, total - (intro + faq + conclusion + cta))
    n_sections = 3 if total < 850 else (4 if total < 1400 else 5)
    per_section = max(160, remaining // n_sections)
    sections_total = per_section * n_sections
    drift = total - (intro + faq + conclusion + cta + sections_total)
    intro += drift
    word_budget: Dict[str, int] = {
        "Introduction": intro, "Sections": per_section, "SectionsCount": n_sections,
        "FAQ": faq, "Conclusion": conclusion,
    }
    if has_business:
        word_budget["CTA"] = cta
    logger.info("WordBudgetAllocator", extra={"word_budget": word_budget})
    return {"word_budget": word_budget}


# ---------------------------------------------------------------------------
# Node: ResearchRouter
# ---------------------------------------------------------------------------

async def research_router(state: BlogState) -> BlogState:
    topic = state["topic"]
    goal = _goal_normalize(state.get("goal"))
    t = topic.lower()
    triggers = [r"\b20\d{2}\b", r"\b(statistics|stats|survey|study|data|trends|forecast|latest|new|update)\b"]
    research_required = goal == "authority" or any(re.search(p, t) for p in triggers)
    logger.info("ResearchRouter", extra={"research_required": research_required})
    return {"research_required": research_required}


# ---------------------------------------------------------------------------
# Node: ResearchNode
# ---------------------------------------------------------------------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
def _tavily_search(api_key: str, query: str) -> Dict[str, Any]:
    client = TavilyClient(api_key=api_key)
    return client.search(query=query, max_results=6, include_answer=True, include_raw_content=False)


async def research_node(state: BlogState) -> BlogState:
    s = get_settings()
    if not s.tavily_api_key:
        logger.warning("ResearchNode: no TAVILY_API_KEY")
        return {"research_data": None}
    topic = state["topic"]
    loc = _safe_str(state.get("location"))
    goal = _goal_normalize(state.get("goal"))
    query = f"{topic} {loc}".strip() + (" statistics trends" if goal == "authority" else "")
    raw = await asyncio.to_thread(_tavily_search, s.tavily_api_key, query)
    chain = _get_editor_llm().bind(max_tokens=words_to_max_tokens(800)).with_structured_output(ResearchSummary)
    prompt = f"""You are a research assistant for a blog. Topic: {topic}. Location: {loc or 'N/A'}. Goal: {goal}.
Extract from the search results into the structured fields: facts (6-10), keywords (4-6), faqs (6), suggested_headings (3), sources (URLs only).
Search results:\n{json.dumps(raw, ensure_ascii=False)}"""
    summary = await chain.ainvoke([SystemMessage(content=prompt)])
    if isinstance(summary, dict):
        summary = ResearchSummary(**summary)
    md_parts = [
        "## Facts\n" + "\n".join(f"- {f}" for f in summary.facts),
        "## Keywords\n" + ", ".join(summary.keywords),
        "## FAQs\n" + "\n".join(f"- {q}" for q in summary.faqs),
        "## Suggested Headings\n" + "\n".join(f"- {h}" for h in summary.suggested_headings),
        "## Sources\n" + "\n".join(summary.sources),
    ]
    research_data = "\n\n".join(md_parts)
    return {"research_data": research_data}


# ---------------------------------------------------------------------------
# Node: Planner
# ---------------------------------------------------------------------------

async def planner(state: BlogState) -> BlogState:
    budget = state["word_budget"]
    n = int(budget["SectionsCount"])
    per_section = int(budget["Sections"])
    has_business = bool(_safe_str(state.get("business_context")))
    loc = _safe_str(state.get("location"))
    intent = state.get("intent", "informational")
    tone = _safe_str(state.get("tone")) or "clear, helpful, direct"
    research_data = (state.get("research_data") or "").strip()
    chain = _get_editor_llm().with_structured_output(Outline)
    prompt = f"""You are a blog planner. Topic: {state['topic']}. Intent: {intent}. Tone: {tone}. Location: {loc or 'N/A'}.
Word budget: Intro {budget['Introduction']}, {n} sections x {per_section}, FAQ {budget['FAQ']}, Conclusion {budget['Conclusion']}{f", CTA {budget.get('CTA')}" if has_business else ""}.
Output an outline with items in order: 1 introduction, {n} sections, 1 faq, 1 conclusion{f", 1 cta" if has_business else ""}.
Each item: kind (introduction|section|faq|conclusion|cta), heading, target_words (match budget), bullets (3-6 strings).
Research (use to inform bullets/headings):\n{research_data}"""
    try:
        out = await chain.ainvoke([SystemMessage(content=prompt)])
        outline_items = out.items if isinstance(out, Outline) else out["items"]
    except Exception:
        outline_items = [
            OutlineItem(kind="introduction", heading="Introduction", target_words=budget["Introduction"],
                        bullets=["Hook", "Preview"]),
        ]
        for i in range(n):
            outline_items.append(OutlineItem(kind="section", heading=f"Section {i + 1}", target_words=per_section,
                                             bullets=["Key idea", "Steps"]))
        outline_items.extend([
            OutlineItem(kind="faq", heading="FAQ", target_words=budget["FAQ"], bullets=["Answers"]),
            OutlineItem(kind="conclusion", heading="Conclusion", target_words=budget["Conclusion"],
                        bullets=["Summary"]),
        ])
        if has_business:
            outline_items.append(
                OutlineItem(kind="cta", heading="Call to Action", target_words=int(budget.get("CTA", 0)),
                            bullets=["Offer", "Contact"]))
    enforced: List[Dict[str, Any]] = []
    for item in outline_items:
        enforced.append({
            "kind": item.kind,
            "heading": item.heading,
            "target_words": item.target_words,
            "bullets": item.bullets,
        })
    # Enforce budget numbers from state
    enforced[0]["target_words"] = int(budget["Introduction"])
    for i, e in enumerate(enforced):
        if e.get("kind") == "section":
            enforced[i]["target_words"] = per_section
        elif e.get("kind") == "faq":
            enforced[i]["target_words"] = int(budget["FAQ"])
        elif e.get("kind") == "conclusion":
            enforced[i]["target_words"] = int(budget["Conclusion"])
        elif e.get("kind") == "cta":
            enforced[i]["target_words"] = int(budget.get("CTA", 0))
    logger.info("Planner", extra={"outline_items": len(enforced)})
    return {"outline": enforced}


# ---------------------------------------------------------------------------
# SectionGenerator helpers
# ---------------------------------------------------------------------------

def _range_from_target(target: int) -> Tuple[int, int]:
    low = max(30, math.floor(target * 0.90))
    high = max(35, math.ceil(target * 1.10))
    return low, high


async def _rewrite_to_target(section_md: str, target_words: int, tone: str) -> str:
    low, high = _range_from_target(target_words)
    editor = _get_editor_llm().bind(max_tokens=words_to_max_tokens(high))
    res = await editor.ainvoke([SystemMessage(
        content=f"Edit this markdown to {low}-{high} words. Tone: {tone}. Return ONLY the revised section.\n\n{section_md}")])
    return (res.content or "").strip()


async def _generate_one_section(state: BlogState, item: Dict[str, Any]) -> str:
    target_words = int(item["target_words"])
    low, high = _range_from_target(target_words)
    writer = _get_writer_llm().bind(max_tokens=words_to_max_tokens(high))
    topic = state["topic"]
    intent = state.get("intent", "informational")
    tone = _safe_str(state.get("tone")) or "clear, helpful, direct"
    goal = _goal_normalize(state.get("goal"))
    loc = _safe_str(state.get("location"))
    biz = _safe_str(state.get("business_context"))
    research = (state.get("research_data") or "").strip()
    heading = item.get("heading") or "Section"
    kind = item.get("kind") or "section"
    heading_md = "" if kind == "introduction" else f"## {heading}\n\n"
    system = f"""You are an expert SEO blog writer. Write ONLY markdown. Stay within {low}-{high} words (output will be capped). Scannable style. Use research if provided. No pitch unless CTA section."""
    user = f"Topic: {topic}. Intent: {intent}. Goal: {goal}. Tone: {tone}. Location: {loc or 'N/A'}. Business: {biz or 'N/A'}.\nSection: {kind} – {heading}. Bullets: {item.get('bullets', [])}.\nResearch:\n{research}\nReturn markdown for this section (include H2 if not intro)."
    res = await writer.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
    section_md = f"{heading_md}{(res.content or '').strip()}".strip()
    if count_words(section_md) < low or count_words(section_md) > high:
        section_md = await _rewrite_to_target(section_md, target_words, tone)
    return section_md.strip()


# ---------------------------------------------------------------------------
# Node: SectionGenerator
# ---------------------------------------------------------------------------

async def section_generator(state: BlogState) -> BlogState:
    outline = state.get("outline") or []
    if not outline:
        raise RuntimeError("Planner produced empty outline.")
    logger.info("SectionGenerator", extra={"count": len(outline)})
    sem = asyncio.Semaphore(4)

    async def run_one(item: Dict[str, Any]) -> str:
        async with sem:
            return await _generate_one_section(state, item)

    sections = await asyncio.gather(*[run_one(item) for item in outline])
    return {"sections": list(sections)}


# ---------------------------------------------------------------------------
# Node: MergeNode
# ---------------------------------------------------------------------------

async def merge_node(state: BlogState) -> BlogState:
    sections = state.get("sections") or []
    if not sections:
        raise RuntimeError("No sections to merge.")
    headings = []
    for s in sections:
        for line in s.splitlines():
            if line.startswith("## "):
                headings.append(line.replace("## ", "").strip())
                break
    toc = f"## Table of Contents\n\n" + "\n".join(f"- {h}" for h in headings) + "\n\n" if headings else ""
    blog_md = (sections[0].strip() + "\n\n" + toc + "\n\n".join(s.strip() for s in sections[1:])).strip()
    return {"blog_markdown": blog_md, "final_blog": blog_md}


# ---------------------------------------------------------------------------
# Node: WordValidator
# ---------------------------------------------------------------------------

async def word_validator(state: BlogState) -> BlogState:
    md_text = state.get("blog_markdown") or state.get("final_blog") or ""
    wc = count_words(md_text)
    logger.info("WordValidator", extra={"word_count": wc})
    return {"word_count": wc}


# ---------------------------------------------------------------------------
# Node: CompressionNode
# ---------------------------------------------------------------------------

async def compression_node(state: BlogState) -> BlogState:
    approx = int(state["approx_max_words"])
    editor = _get_editor_llm().bind(max_tokens=words_to_max_tokens(approx))
    blog_md = state.get("blog_markdown") or ""
    res = await editor.ainvoke([SystemMessage(
        content=f"Compress this blog to <= {approx} words. Keep structure, FAQ, CTA if present. Return ONLY markdown.\n\n{blog_md}")])
    compressed = (res.content or "").strip()
    return {"blog_markdown": compressed, "final_blog": compressed}


# ---------------------------------------------------------------------------
# Node: SEOOptimizer
# ---------------------------------------------------------------------------

async def seo_optimizer(state: BlogState) -> BlogState:
    topic = state["topic"]
    loc = _safe_str(state.get("location"))
    tone = _safe_str(state.get("tone")) or "clear, helpful, direct"
    goal = _goal_normalize(state.get("goal"))
    md_in = (state.get("final_blog") or state.get("blog_markdown") or "").strip()
    approx = int(state["approx_max_words"])
    chain = _get_editor_llm().bind(max_tokens=words_to_max_tokens(int(approx * 1.05))).with_structured_output(SEOOutput)
    prompt = f"""SEO editor. Topic: {topic}. Goal: {goal}. Tone: {tone}. Location: {loc or 'N/A'}.
Improve keyword placement. optimized_markdown must not exceed {int(approx * 1.02)} words. meta_title <= 60 chars, meta_description <= 155 chars, slug URL-safe.
Input blog:\n{md_in}"""
    out = await chain.ainvoke([SystemMessage(content=prompt)])
    if isinstance(out, dict):
        out = SEOOutput(**out)
    optimized = (out.optimized_markdown or md_in).strip()
    return {
        "final_blog": optimized,
        "meta_title": (out.meta_title or topic).strip()[:80],
        "meta_description": (out.meta_description or "").strip()[:220],
        "slug": slugify((out.slug or out.meta_title or topic).strip()),
    }


# ---------------------------------------------------------------------------
# Node: QualityScorer
# ---------------------------------------------------------------------------

async def quality_scorer(state: BlogState) -> BlogState:
    chain = _get_editor_llm().with_structured_output(QualityScore)
    threshold = get_settings().quality_threshold
    blog = state.get("final_blog") or ""
    out = await chain.ainvoke(
        [SystemMessage(content=f"Score this blog 1-10 for intent, structure, SEO, depth, word discipline.\n\n{blog}")])
    score = float(out.score) if hasattr(out, "score") else float(out.get("score", 6))
    logger.info("QualityScorer", extra={"quality_score": score, "threshold": threshold})
    return {"quality_score": score}


# ---------------------------------------------------------------------------
# Node: EmailFormatter
# ---------------------------------------------------------------------------

async def email_formatter(state: BlogState) -> BlogState:
    chain = _get_editor_llm().with_structured_output(SubjectLine)
    topic = state["topic"]
    meta_title = _safe_str(state.get("meta_title")) or topic
    goal = _goal_normalize(state.get("goal"))
    tone = _safe_str(state.get("tone")) or "clear, helpful, direct"
    out = await chain.ainvoke([SystemMessage(
        content=f"One email subject line, <=70 chars. Topic: {topic}. Meta: {meta_title}. Goal: {goal}. Tone: {tone}. No quotes.")])
    subject = (out.subject if hasattr(out, "subject") else out.get("subject", meta_title)).strip()
    subject = re.sub(r"\s+", " ", subject.replace("\n", " "))[:90]
    html_body = markdown_to_html(state.get("final_blog") or "")
    html_body = _inline_styles_for_email(html_body)
    meta_desc = _safe_str(state.get("meta_description"))
    email_html = _medium_style_html(
        title=_bleach_safe(meta_title),
        description=meta_desc and _bleach_safe(meta_desc),
        body_html=html_body,
    )
    return {"email_subject": subject, "email_html": email_html}


# ---------------------------------------------------------------------------
# Node: EmailSender
# ---------------------------------------------------------------------------

@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _send_resend(api_key: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.post("https://api.resend.com/emails",
                                 headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                                 json=payload)
        if resp.status_code in (429, 500, 502, 503, 504):
            raise RuntimeError(f"Resend error {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()
        return resp.json()


async def email_sender(state: BlogState) -> BlogState:
    global LAST_EMAIL_RESULT
    s = get_settings()
    if not s.resend_api_key:
        logger.warning("EmailSender: no RESEND_API_KEY")
        LAST_EMAIL_RESULT = {"status": "skipped", "reason": "missing_resend_api_key"}
        return {}
    to_email = [e.strip() for e in state["recipient_email"]]
    subject = state.get("email_subject") or state.get("meta_title") or state["topic"]
    html = state.get("email_html") or ""
    payload = {"from": s.resend_from_email, "to": to_email, "subject": subject, "html": html}
    try:
        result = await _send_resend(s.resend_api_key, payload)
        LAST_EMAIL_RESULT = {"status": "sent", "id": result.get("id")}
        logger.info("EmailSender: sent", extra={"id": result.get("id")})
    except Exception as e:
        logger.exception("EmailSender: failed")
        LAST_EMAIL_RESULT = {"status": "failed", "error": str(e)}
    return {}
