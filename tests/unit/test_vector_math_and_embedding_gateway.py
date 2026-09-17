import pytest
from packages.model_gateway.gateway import ModelGateway
from packages.model_gateway.mock_adapter import MockModelAdapter
from packages.model_gateway.settings_manager import EmbeddingConfigItem, ModelSettingsConfig, SettingsManager
from packages.retrieval.vector_math import (
    cosine_similarity,
    dot_product,
    l2_normalize,
    pack_vector,
    reciprocal_rank_fusion,
    unpack_vector,
)
from packages.retrieval.vector_service import VectorService


def test_vector_math_pack_and_unpack():
    vec = [0.123456, -0.654321, 0.0, 1.0, -1.0]
    blob = pack_vector(vec)
    assert isinstance(blob, bytes)
    assert len(blob) == len(vec) * 4  # 4 bytes per float32

    unpacked = unpack_vector(blob)
    assert len(unpacked) == len(vec)
    for orig, res in zip(vec, unpacked):
        assert abs(orig - res) < 1e-5


def test_vector_math_l2_normalize():
    vec = [3.0, 4.0]
    normed = l2_normalize(vec)
    assert abs(normed[0] - 0.6) < 1e-5
    assert abs(normed[1] - 0.8) < 1e-5

    # Zero vector safety
    zero_vec = [0.0, 0.0, 0.0]
    normed_zero = l2_normalize(zero_vec)
    assert normed_zero == [0.0, 0.0, 0.0]


def test_vector_math_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]
    v4 = [-1.0, 0.0, 0.0]

    assert abs(cosine_similarity(v1, v2) - 1.0) < 1e-5
    assert abs(cosine_similarity(v1, v3) - 0.0) < 1e-5
    assert abs(cosine_similarity(v1, v4) - (-1.0)) < 1e-5

    # Empty vector handling
    assert cosine_similarity([], v1) == 0.0
    assert cosine_similarity(v1, []) == 0.0


def test_reciprocal_rank_fusion():
    sparse_ranks = [("item_A", 0.9), ("item_B", 0.8), ("item_C", 0.5)]
    dense_ranks = [("item_B", 0.95), ("item_D", 0.85), ("item_A", 0.7)]

    fused = reciprocal_rank_fusion(sparse_ranks, dense_ranks, k=60)
    assert len(fused) == 4

    # item_B was rank 2 in sparse and rank 1 in dense -> should score highest
    top_item, top_score = fused[0]
    assert top_item == "item_B"
    assert top_score > 0.0


@pytest.mark.asyncio
async def test_mock_embedding_provider():
    adapter = MockModelAdapter()
    texts = [
        "界面与显示 · 谱面字体太小看不清",
        "界面与显示 · 字号偏小辨识度低",
        "固件升级 · OTA升级进度卡在99%",
    ]
    vecs, usage = await adapter.generate_embeddings(texts)
    assert len(vecs) == 3
    assert len(vecs[0]) == 1536
    assert usage["total_tokens"] > 0

    # Semantic similarity check
    sim_similar = cosine_similarity(vecs[0], vecs[1])
    sim_different = cosine_similarity(vecs[0], vecs[2])
    assert sim_similar > sim_different
    assert sim_similar > 0.3
    assert sim_different < 0.15


@pytest.mark.asyncio
async def test_vector_service_caching():
    vs = VectorService(session=None)
    text = "伴奏风格包自定义配置"

    vec1 = await vs.embed_single(text, force_mock=True)
    assert len(vec1) == 1536

    # Second call should hit memory cache without recomputing
    cache_key = (VectorService.compute_sha256(text), "text-embedding-v3")
    assert cache_key in vs._memory_cache

    vec2 = await vs.embed_single(text, force_mock=True)
    assert vec1 == vec2


def test_embedding_config_settings_integration():
    settings = SettingsManager.get_settings()
    assert hasattr(settings, "embedding")
    assert settings.embedding.enabled is True
    assert settings.embedding.model == "text-embedding-v3"
    assert settings.embedding.dimension == 1536
