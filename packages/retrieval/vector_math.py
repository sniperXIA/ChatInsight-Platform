import math
import struct
from typing import Any, Sequence, TypeVar

T = TypeVar("T")


def pack_vector(vec: Sequence[float]) -> bytes:
    """Serializes a float vector into compact binary IEEE 754 float32 bytes."""
    if not vec:
        return b""
    return struct.pack(f"{len(vec)}f", *vec)


def unpack_vector(blob: bytes) -> list[float]:
    """Deserializes binary IEEE 754 float32 bytes back into a float list."""
    if not blob:
        return []
    count = len(blob) // 4
    return list(struct.unpack(f"{count}f", blob))


def l2_normalize(vec: Sequence[float]) -> list[float]:
    """Normalizes a vector to unit length (L2 norm = 1.0)."""
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 1e-12:
        return [0.0] * len(vec)
    return [x / norm for x in vec]


def dot_product(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Computes the dot product between two equal-length vectors."""
    n = min(len(v1), len(v2))
    return sum(v1[i] * v2[i] for i in range(n))


def cosine_similarity(v1: Sequence[float], v2: Sequence[float]) -> float:
    """Computes cosine similarity between two vectors in [-1.0, 1.0]."""
    if not v1 or not v2:
        return 0.0
    n = min(len(v1), len(v2))
    dot = 0.0
    norm1 = 0.0
    norm2 = 0.0
    for i in range(n):
        x = v1[i]
        y = v2[i]
        dot += x * y
        norm1 += x * x
        norm2 += y * y

    if norm1 <= 1e-12 or norm2 <= 1e-12:
        return 0.0

    return max(-1.0, min(1.0, dot / (math.sqrt(norm1) * math.sqrt(norm2))))


def reciprocal_rank_fusion(
    sparse_items: Sequence[tuple[T, float]],
    dense_items: Sequence[tuple[T, float]],
    k: int = 60,
    weight_sparse: float = 1.0,
    weight_dense: float = 1.0,
) -> list[tuple[T, float]]:
    """
    Reciprocal Rank Fusion (RRF) combining sparse lexical ranking and dense semantic ranking.
    RRF Score = weight_sparse / (k + rank_sparse) + weight_dense / (k + rank_dense)
    """
    scores: dict[Any, float] = {}
    item_map: dict[Any, T] = {}

    # Rank sparse items (1-indexed)
    for rank, (item, _) in enumerate(sparse_items, 1):
        key = getattr(item, "id", None) or item
        item_map[key] = item
        scores[key] = scores.get(key, 0.0) + (weight_sparse / (k + rank))

    # Rank dense items (1-indexed)
    for rank, (item, _) in enumerate(dense_items, 1):
        key = getattr(item, "id", None) or item
        item_map[key] = item
        scores[key] = scores.get(key, 0.0) + (weight_dense / (k + rank))

    ranked = [(item_map[key], round(score, 6)) for key, score in scores.items()]
    ranked.sort(key=lambda x: x[1], reverse=True)
    return ranked
