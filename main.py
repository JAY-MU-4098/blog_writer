"""
FastAPI app for the blog writer pipeline. Run locally with:
  uvicorn main:app --reload --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from run import run_pipeline_async

app = FastAPI(
    title="Blog Writer API",
    description="Generate SEO-optimized blog posts from a topic and optional context.",
    version="1.0.0",
)


class BlogGenerateRequest(BaseModel):
    topic: str = Field(..., description="Blog topic")
    approx_max_words: int = Field(1200, ge=200, le=5000, description="Approximate max word count")
    recipient_email: Optional[List[str]] = Field(default_factory=list, description="Optional: email address(es) to send the blog to (email sent only if non-empty)")
    goal: Optional[str] = Field("traffic", description="Goal: traffic, leads, or authority")
    tone: Optional[str] = Field("helpful, direct", description="Tone description")
    location: Optional[str] = Field(None, description="Optional location for local SEO")
    business_context: Optional[str] = Field(None, description="Optional business context")


class BlogGenerateResponse(BaseModel):
    final_blog: str
    meta_title: Optional[str] = None
    meta_description: Optional[str] = None
    slug: Optional[str] = None
    word_count: Optional[int] = None
    quality_score: Optional[float] = None
    email_subject: Optional[str] = None
    email_html: Optional[str] = None
    saved_html_path: Optional[str] = None


@app.get("/")
def root() -> Dict[str, str]:
    return {"message": "Blog Writer API", "docs": "/docs", "health": "/health"}


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/generate", response_model=BlogGenerateResponse)
async def generate_blog(req: BlogGenerateRequest) -> BlogGenerateResponse:
    """Generate a full blog post from the given topic and options."""
    try:
        result = await run_pipeline_async(
            topic=req.topic,
            approx_max_words=req.approx_max_words,
            recipient_email=req.recipient_email,
            goal=req.goal,
            tone=req.tone,
            location=req.location,
            business_context=req.business_context,
        )
        return BlogGenerateResponse(
            final_blog=result.get("final_blog", ""),
            meta_title=result.get("meta_title"),
            meta_description=result.get("meta_description"),
            slug=result.get("slug"),
            word_count=result.get("word_count"),
            quality_score=result.get("quality_score"),
            email_subject=result.get("email_subject"),
            email_html=result.get("email_html"),
            saved_html_path=result.get("saved_html_path"),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
