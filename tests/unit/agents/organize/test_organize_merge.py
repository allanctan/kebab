"""Unit tests for the incremental organize merge logic."""

from __future__ import annotations

import pytest

from app.agents.organize.agent import HierarchyNode, HierarchyPlan
from app.agents.organize import (
    _covered_ids,
    _merge_plans,
    _select_new_manifest,
)


def _node(
    id: str,
    name: str,
    level_type: str,
    *,
    parent_id: str | None = None,
    source_files: list[int] | None = None,
) -> HierarchyNode:
    return HierarchyNode(
        id=id,
        name=name,
        level_type=level_type,  # type: ignore[arg-type]
        parent_id=parent_id,
        description=f"{name} description",
        source_files=source_files or [],
    )


def _base_plan() -> HierarchyPlan:
    return HierarchyPlan(
        nodes=[
            _node("SCI", "Science", "domain"),
            _node("SCI-BIO", "Biology", "subdomain", parent_id="SCI"),
            _node(
                "SCI-BIO-001",
                "Photosynthesis",
                "article",
                parent_id="SCI-BIO",
                source_files=[1],
            ),
        ]
    )


class TestCoveredIds:
    def test_collects_ids_across_article_nodes(self) -> None:
        plan = HierarchyPlan(
            nodes=[
                _node("SCI", "Science", "domain"),
                _node(
                    "SCI-BIO-001",
                    "Photosynthesis",
                    "article",
                    parent_id="SCI",
                    source_files=[1, 2],
                ),
                _node(
                    "SCI-BIO-002",
                    "Respiration",
                    "article",
                    parent_id="SCI",
                    source_files=[3],
                ),
            ]
        )
        assert _covered_ids(plan) == {1, 2, 3}

    def test_ignores_non_article_nodes(self) -> None:
        plan = HierarchyPlan(
            nodes=[
                _node("SCI", "Science", "domain"),
                # Topic nodes can't carry sources in practice, but the
                # filter still skips them deterministically.
                _node("SCI-TOP", "Topic", "topic", parent_id="SCI"),
            ]
        )
        assert _covered_ids(plan) == set()


class TestSelectNewManifest:
    def test_only_returns_uncovered_entries(self) -> None:
        plan = _base_plan()
        manifest = [
            ("[1] Openstax Chapter 8", "snippet a"),  # already covered (id=1)
            ("[2] New Source", "snippet b"),           # net new
            ("no_id_label", "snippet c"),              # no [N] prefix — always included
        ]
        result = _select_new_manifest(plan, manifest)
        assert [name for name, _ in result] == ["[2] New Source", "no_id_label"]


class TestMergePlans:
    def test_extends_source_files_on_existing_article(self) -> None:
        existing = _base_plan()
        update = HierarchyPlan(
            nodes=[
                _node(
                    "SCI-BIO-001",
                    "Photosynthesis",
                    "article",
                    parent_id="SCI-BIO",
                    source_files=[2],
                )
            ]
        )
        merged, extended, added = _merge_plans(existing, update)
        photo = next(n for n in merged.nodes if n.id == "SCI-BIO-001")
        assert photo.source_files == [1, 2]
        assert extended == ["SCI-BIO-001"]
        assert added == []

    def test_dedupes_source_files_on_extension(self) -> None:
        existing = _base_plan()
        update = HierarchyPlan(
            nodes=[
                _node(
                    "SCI-BIO-001",
                    "Photosynthesis",
                    "article",
                    parent_id="SCI-BIO",
                    source_files=[1],  # already present
                )
            ]
        )
        merged, extended, added = _merge_plans(existing, update)
        photo = next(n for n in merged.nodes if n.id == "SCI-BIO-001")
        assert photo.source_files == [1]
        # No-op extension must NOT be reported as extended.
        assert extended == []
        assert added == []

    def test_appends_new_article_under_existing_parent(self) -> None:
        existing = _base_plan()
        update = HierarchyPlan(
            nodes=[
                _node(
                    "SCI-BIO-002",
                    "Cellular Respiration",
                    "article",
                    parent_id="SCI-BIO",
                    source_files=[3],
                )
            ]
        )
        merged, extended, added = _merge_plans(existing, update)
        assert added == ["SCI-BIO-002"]
        assert extended == []
        assert "SCI-BIO-002" in {n.id for n in merged.nodes}
        # Original nodes are preserved in order.
        assert [n.id for n in merged.nodes][:3] == ["SCI", "SCI-BIO", "SCI-BIO-001"]

    def test_appends_new_article_with_new_parent_in_same_update(self) -> None:
        existing = _base_plan()
        update = HierarchyPlan(
            nodes=[
                _node("SCI-CHE", "Chemistry", "subdomain", parent_id="SCI"),
                _node(
                    "SCI-CHE-001",
                    "Atomic Structure",
                    "article",
                    parent_id="SCI-CHE",
                    source_files=[4],
                ),
            ]
        )
        merged, extended, added = _merge_plans(existing, update)
        assert added == ["SCI-CHE-001"]
        ids = {n.id for n in merged.nodes}
        assert {"SCI-CHE", "SCI-CHE-001"} <= ids

    def test_drops_nodes_with_dangling_parent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        existing = _base_plan()
        update = HierarchyPlan(
            nodes=[
                _node(
                    "SCI-XXX-001",
                    "Orphan",
                    "article",
                    parent_id="SCI-XXX",  # does not exist anywhere
                    source_files=[5],
                )
            ]
        )
        with caplog.at_level("WARNING"):
            merged, extended, added = _merge_plans(existing, update)
        assert added == []
        assert "SCI-XXX-001" not in {n.id for n in merged.nodes}
        assert any("dangling parent" in rec.message for rec in caplog.records)

    def test_preserves_existing_order(self) -> None:
        existing = _base_plan()
        update = HierarchyPlan(
            nodes=[
                _node(
                    "SCI-BIO-002",
                    "Respiration",
                    "article",
                    parent_id="SCI-BIO",
                    source_files=[6],
                )
            ]
        )
        merged, _, _ = _merge_plans(existing, update)
        assert [n.id for n in merged.nodes] == [
            "SCI",
            "SCI-BIO",
            "SCI-BIO-001",
            "SCI-BIO-002",
        ]

    def test_level_type_mismatch_skips_extension(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        existing = _base_plan()
        update = HierarchyPlan(
            nodes=[
                # LLM returned the right id but wrong level_type. Refuse to
                # corrupt the existing tree: log a warning and skip.
                _node(
                    "SCI-BIO-001",
                    "Photosynthesis",
                    "topic",  # was "article"
                    parent_id="SCI-BIO",
                )
            ]
        )
        with caplog.at_level("WARNING"):
            merged, extended, added = _merge_plans(existing, update)
        photo = next(n for n in merged.nodes if n.id == "SCI-BIO-001")
        assert photo.level_type == "article"  # unchanged
        assert photo.source_files == [1]  # unchanged
        assert extended == []
        assert any("level_type mismatch" in rec.message for rec in caplog.records)
