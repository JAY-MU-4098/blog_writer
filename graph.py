"""
LangGraph definition: one file. Imports nodes and builds the compiled graph.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from nodes import (
    BlogState,
    compression_node,
    context_analyzer,
    email_formatter,
    email_sender,
    get_settings,
    merge_node,
    planner,
    quality_scorer,
    research_node,
    research_router,
    save_blog_html,
    section_generator,
    seo_optimizer,
    word_budget_allocator,
    word_validator,
)


def build_graph():
    graph = StateGraph(BlogState)
    graph.add_node("ContextAnalyzer", context_analyzer)
    graph.add_node("WordBudgetAllocator", word_budget_allocator)
    graph.add_node("ResearchRouter", research_router)
    graph.add_node("ResearchNode", research_node)
    graph.add_node("Planner", planner)
    graph.add_node("SectionGenerator", section_generator)
    graph.add_node("MergeNode", merge_node)
    graph.add_node("WordValidator", word_validator)
    graph.add_node("CompressionNode", compression_node)
    graph.add_node("SEOOptimizer", seo_optimizer)
    graph.add_node("QualityScorer", quality_scorer)
    graph.add_node("EmailFormatter", email_formatter)
    graph.add_node("SaveBlogHtml", save_blog_html)
    graph.add_node("EmailSender", email_sender)

    graph.set_entry_point("ContextAnalyzer")
    graph.add_edge("ContextAnalyzer", "WordBudgetAllocator")
    graph.add_edge("WordBudgetAllocator", "ResearchRouter")

    def route_research(state: BlogState) -> str:
        return "ResearchNode" if state.get("research_required") else "Planner"

    graph.add_conditional_edges("ResearchRouter", route_research, {"ResearchNode": "ResearchNode", "Planner": "Planner"})
    graph.add_edge("ResearchNode", "Planner")
    graph.add_edge("Planner", "SectionGenerator")
    graph.add_edge("SectionGenerator", "MergeNode")
    graph.add_edge("MergeNode", "WordValidator")

    def route_compress(state: BlogState) -> str:
        wc = int(state.get("word_count") or 0)
        limit = int(state["approx_max_words"])
        return "CompressionNode" if wc > int(limit * 1.05) else "SEOOptimizer"

    graph.add_conditional_edges("WordValidator", route_compress, {"CompressionNode": "CompressionNode", "SEOOptimizer": "SEOOptimizer"})
    graph.add_edge("CompressionNode", "SEOOptimizer")
    graph.add_edge("SEOOptimizer", "QualityScorer")

    def route_quality(state: BlogState) -> str:
        threshold = float(get_settings().quality_threshold)
        score = float(state.get("quality_score") or 0)
        return "SEOOptimizer" if score < threshold else "EmailFormatter"

    graph.add_conditional_edges("QualityScorer", route_quality, {"SEOOptimizer": "SEOOptimizer", "EmailFormatter": "EmailFormatter"})
    graph.add_edge("EmailFormatter", "SaveBlogHtml")
    graph.add_edge("SaveBlogHtml", "EmailSender")
    graph.add_edge("EmailSender", END)

    return graph.compile()
