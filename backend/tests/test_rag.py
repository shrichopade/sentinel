import pytest
from rag.retrieval import retrieve, keyword_search, embed
from rag.regulatory import retrieve_regulatory_context


@pytest.mark.asyncio
async def test_semantic_search_returns_results():
    result = await retrieve("when does this contract expire?")
    assert isinstance(result, list)
    if len(result) == 0:
        pytest.skip("Skip: no documents ingested yet — run /ingest first")
    assert all("id" in r and "content" in r and "document_id" in r for r in result)


def test_keyword_fallback():
    result = keyword_search("cancellation")
    assert isinstance(result, list)
    assert all("content" in r for r in result)


def test_regulatory_rag_returns_results():
    result = retrieve_regulatory_context("cancellation rights", "GB", "subscription")
    assert isinstance(result, list)
    if len(result) == 0:
        pytest.fail("Regulatory corpus is empty — run POST /regulatory/seed first")
    assert all("regulation_name" in r and "content" in r and "section_ref" in r for r in result)


def test_embed_returns_correct_dimensions():
    result = embed("test document query")
    assert isinstance(result, list)
    assert len(result) == 1024
    assert all(isinstance(v, float) for v in result)
