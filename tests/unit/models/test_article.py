from app.models import Article, ContextMapping


def test_article_minimal() -> None:
    a = Article(
        id="SCI-BIO-002",
        name="Photosynthesis",
        description="How plants convert light energy into chemical energy.",
        level_type="article",
        depth=3,
        domain="Science",
        subdomain="Biology",
        confidence_level=3,
    )
    assert a.id == "SCI-BIO-002"
    assert a.confidence_level == 3
    assert a.keywords == []
    assert a.faq == []


def test_article_contexts_accept_arbitrary_keys() -> None:
    ctx = ContextMapping.model_validate({"ph_k12": {"grade": 7}})
    a = Article(
        id="SCI-BIO-002",
        name="Photosynthesis",
        description="…",
        level_type="article",
        depth=3,
        domain="Science",
        confidence_level=3,
        contexts=ctx,
    )
    assert a.contexts.model_dump()["ph_k12"]["grade"] == 7
