"""
Reciprocal Rank Fusion (RRF) for merging ranked lists from vector search and BM25.

RRF is a simple, effective method for combining multiple ranked lists without
requiring score normalization. The formula for each document d is:

    RRF_score(d) = Σ 1 / (k + rank(d, list_i))

where k=60 is a constant that diminishes the impact of very high-ranked results.

References:
    - Cormack, Clarke & Buettcher (2009): "Reciprocal Rank Fusion outperforms
      Condorcet and individual Rank Learning Methods"
"""

from __future__ import annotations

import uuid
import logging

logger = logging.getLogger(__name__)

# RRF constant — standard value recommended in the original paper
_RRF_K = 60


def reciprocal_rank_fusion(
    vector_ids: list[uuid.UUID],
    bm25_ids: list[uuid.UUID],
    k: int = _RRF_K,
) -> list[uuid.UUID]:
    """
    Merge two ranked lists via Reciprocal Rank Fusion.

    Args:
        vector_ids: Chunk UUIDs ranked by vector similarity (best first).
        bm25_ids: Chunk UUIDs ranked by BM25 score (best first).
        k: RRF constant (default 60, from original paper).

    Returns:
        Merged list of chunk UUIDs ranked by combined RRF score (best first).
    """
    scores: dict[uuid.UUID, float] = {}

    def _apply_ranks(ranked_list: list[uuid.UUID]) -> None:
        for rank, chunk_id in enumerate(ranked_list, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)

    _apply_ranks(vector_ids)
    _apply_ranks(bm25_ids)

    # Sort by combined RRF score descending
    merged = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)

    logger.debug(
        "RRF fusion complete",
        extra={
            "vector_candidates": len(vector_ids),
            "bm25_candidates": len(bm25_ids),
            "merged_unique": len(merged),
        },
    )

    return merged
