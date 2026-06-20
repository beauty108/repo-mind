"""
Query decomposition service.

Breaks complex, multi-part questions into focused sub-queries, retrieves
context for each independently, then synthesizes a unified answer.

Example:
    "How does the auth flow work from React frontend to database?"
    → Q1: "How does React handle login state and authentication?"
    → Q2: "What does the auth API endpoint do?"
    → Q3: "How does the backend validate JWT tokens and query the database?"
    → Synthesis: unified answer from all three

This dramatically improves answer quality for cross-cutting questions that
span multiple files/modules — pure vector search on the combined question
often misses relevant context in individual parts.

The decomposer uses the same LLM but with a short, cheap meta-prompt.
Simple questions are returned as-is (list of length 1).
"""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)

# Meta-prompt for the decomposition LLM call
_DECOMPOSE_SYSTEM_PROMPT = """You are a query decomposition assistant for a code search system.
Your ONLY job is to decide whether a question is complex enough to benefit from
being split into multiple focused sub-queries.

Rules:
1. If the question is simple and focused (about one concept/file/function), return it as-is.
2. If the question is complex and spans multiple components/layers/concepts, split it into 2-4 sub-queries.
3. Each sub-query must be self-contained and answerable independently from code context.
4. Return ONLY a JSON array of strings. No explanation, no markdown, no extra text.

Examples:
  Input: "What does the calculate_tax function do?"
  Output: ["What does the calculate_tax function do?"]

  Input: "How does authentication work from the React login form to the database?"
  Output: [
    "How does the React login form handle authentication and what API does it call?",
    "What does the auth API endpoint do and what does it validate?",
    "How does the backend validate credentials and interact with the database?"
  ]"""


async def decompose_query(question: str, max_sub_queries: int = 3) -> list[str]:
    """
    Decompose a complex question into focused sub-queries.

    Args:
        question: The user's original question.
        max_sub_queries: Maximum number of sub-queries to return.

    Returns:
        List of sub-query strings. Returns [question] for simple questions.
        Always returns at least 1 element (the original question on any failure).
    """
    from app.config import get_settings
    settings = get_settings()

    if not settings.query_decomposition_enabled:
        return [question]

    # Skip decomposition for very short questions (likely already focused)
    if len(question.split()) <= 8:
        logger.debug("Question too short for decomposition, using as-is")
        return [question]

    try:
        from app.ai.llm_client import get_llm_client, LLMError

        llm = get_llm_client()
        prompt = f'Question: "{question}"\n\nOutput (JSON array only):'

        # Collect the full response (non-streaming for this meta-call)
        tokens = []
        async for token in llm.generate(
            prompt,
            stream=False,
            system_prompt=_DECOMPOSE_SYSTEM_PROMPT,
        ):
            tokens.append(token)

        raw = "".join(tokens).strip()

        # Parse the JSON array from the response
        sub_queries = _parse_sub_queries(raw, question, max_sub_queries)

        logger.info(
            "Query decomposed",
            extra={
                "original": question,
                "sub_queries": sub_queries,
                "count": len(sub_queries),
            },
        )
        return sub_queries

    except Exception as e:
        logger.warning(
            "Query decomposition failed, using original question",
            extra={"error": str(e)},
        )
        return [question]


def _parse_sub_queries(
    raw_response: str,
    original_question: str,
    max_sub_queries: int,
) -> list[str]:
    """
    Parse sub-queries from the LLM's JSON response.
    Falls back to the original question on any parse failure.
    """
    # Try to extract JSON array from the response
    # The LLM may include markdown code fences or extra text
    json_match = re.search(r"\[.*?\]", raw_response, re.DOTALL)
    if not json_match:
        return [original_question]

    try:
        parsed = json.loads(json_match.group(0))
        if not isinstance(parsed, list):
            return [original_question]

        # Filter to strings only, strip whitespace, remove empties
        queries = [q.strip() for q in parsed if isinstance(q, str) and q.strip()]

        if not queries:
            return [original_question]

        # Cap at max_sub_queries
        return queries[:max_sub_queries]

    except json.JSONDecodeError:
        return [original_question]
