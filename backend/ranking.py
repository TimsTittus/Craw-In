import os
from typing import List, Dict

# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion (RRF)
# ---------------------------------------------------------------------------

def compute_rrf(dense_ranks: List[str], sparse_ranks: List[str], k: int = 60) -> Dict[str, float]:
    """Merge two ranking lists using Reciprocal Rank Fusion.

    Args:
        dense_ranks: Ordered list of document IDs from the dense (vector) search.
        sparse_ranks: Ordered list of document IDs from the sparse (keyword) search.
        k: Fusion constant – higher values give less weight to lower ranks.

    Returns:
        A dictionary mapping ``doc_id`` to its RRF score, sorted in descending
        order of relevance.
    """
    scores: Dict[str, float] = {}
    # Dense (semantic) ranks
    for rank, doc_id in enumerate(dense_ranks):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    # Sparse (keyword) ranks
    for rank, doc_id in enumerate(sparse_ranks):
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
    # Return a dict ordered by score descending
    return dict(sorted(scores.items(), key=lambda item: item[1], reverse=True))

# ---------------------------------------------------------------------------
# Cross‑Encoder Reranker (optional – lazy load)
# ---------------------------------------------------------------------------

_CROSS_ENCODER = None

def _load_cross_encoder():
    """Load the local cross‑encoder model the first time it is needed.

    The model ``cross-encoder/ms-marco-TinyBERT-L-2`` is a lightweight BGE‑Reranker
    that works well for short passages. This function imports ``sentence_transformers``
    lazily to avoid unnecessary startup cost if the reranker is never invoked.
    """
    global _CROSS_ENCODER
    if _CROSS_ENCODER is None:
        try:
            from sentence_transformers import CrossEncoder
            # Using a small, fast model – change the identifier if you need a larger one.
            model_name = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-TinyBERT-L-2")
            _CROSS_ENCODER = CrossEncoder(model_name)
        except Exception as exc:
            raise RuntimeError(
                "Cross‑Encoder model could not be loaded. Ensure 'sentence_transformers' is installed "
                "and the model identifier is correct."
            ) from exc
    return _CROSS_ENCODER

def rerank_docs(query: str, doc_texts: List[str], top_k: int = 10) -> List[int]:
    """Re‑score a list of documents for *query* using a cross‑encoder.

    Returns a list of indices (relative to ``doc_texts``) ordered by decreasing relevance.
    """
    if not doc_texts:
        return []
    try:
        cross_encoder = _load_cross_encoder()
    except RuntimeError as e:
        # If the model cannot be loaded, fall back to original ordering.
        return list(range(min(top_k, len(doc_texts))))

    # Prepare (query, passage) pairs for the model.
    pairs = [(query, txt) for txt in doc_texts]
    scores = cross_encoder.predict(pairs)
    # Get top‑k indices sorted by score descending.
    ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
    return ranked

__all__ = ["compute_rrf", "rerank_docs"]
