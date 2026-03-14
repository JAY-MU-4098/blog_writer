"""
Run the blog writer graph: function API + CLI.
"""
from __future__ import annotations

import argparse
from typing import Any, Dict, List, Optional, Union

from graph import build_graph


def _normalize_recipient_email(recipient_email: Union[str, List[str]]) -> List[str]:
    if isinstance(recipient_email, str):
        return [e.strip() for e in recipient_email.split(",") if e.strip()]
    return [e.strip() for e in recipient_email if e and str(e).strip()]


def run_pipeline(
    topic: str,
    approx_max_words: int,
    recipient_email: Union[str, List[str]],
    goal: Optional[str] = None,
    tone: Optional[str] = None,
    location: Optional[str] = None,
    business_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the blog pipeline synchronously.
    Returns the final state dict (includes final_blog, email_html, email_subject, meta_title, etc.).
    Also sets result["email"] = email_html for convenience.
    """
    graph = build_graph()
    state: Dict[str, Any] = {
        "topic": topic.strip(),
        "approx_max_words": int(approx_max_words),
        "recipient_email": _normalize_recipient_email(recipient_email),
    }
    if goal is not None:
        state["goal"] = goal.strip() or None
    if tone is not None:
        state["tone"] = tone.strip() or None
    if location is not None:
        state["location"] = location.strip() or None
    if business_context is not None:
        state["business_context"] = business_context.strip() or None

    result = graph.invoke(state)
    result["email"] = result.get("email_html")
    return result


async def run_pipeline_async(
    topic: str,
    approx_max_words: int,
    recipient_email: Union[str, List[str]],
    goal: Optional[str] = None,
    tone: Optional[str] = None,
    location: Optional[str] = None,
    business_context: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Run the blog pipeline asynchronously.
    Returns the final state dict (includes final_blog, email_html, etc.).
    """
    graph = build_graph()
    state: Dict[str, Any] = {
        "topic": topic.strip(),
        "approx_max_words": int(approx_max_words),
        "recipient_email": _normalize_recipient_email(recipient_email),
    }
    if goal is not None:
        state["goal"] = goal.strip() or None
    if tone is not None:
        state["tone"] = tone.strip() or None
    if location is not None:
        state["location"] = location.strip() or None
    if business_context is not None:
        state["business_context"] = business_context.strip() or None

    result = await graph.ainvoke(state)
    result["email"] = result.get("email_html")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the blog writer pipeline")
    parser.add_argument("--topic", required=True, help="Blog topic")
    parser.add_argument("--approx-max-words", type=int, default=1200, help="Approximate max word count")
    parser.add_argument("--recipient-email", required=True, help="Email address (or comma-separated list)")
    parser.add_argument("--goal", default="traffic", help="Goal: traffic, leads, or authority")
    parser.add_argument("--tone", default="helpful, direct", help="Tone description")
    parser.add_argument("--location", default=None, help="Optional location for local SEO")
    parser.add_argument("--business-context", default=None, help="Optional business context")
    args = parser.parse_args()

    result = run_pipeline(
        topic=args.topic,
        approx_max_words=args.approx_max_words,
        recipient_email=args.recipient_email,
        goal=args.goal or None,
        tone=args.tone or None,
        location=args.location,
        business_context=args.business_context,
    )
    print(result.get("final_blog", ""))
    if result.get("email"):
        print("\n--- Email HTML prepared (see result['email']). ---")


if __name__ == "__main__":
    main()