"""Tests for news_reader — union, dedup, top-K, catalog error resilience.

PyIceberg and PyArrow are mocked so no live catalog is required.
"""
from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pyarrow as pa

from llm_agent.config import Settings
from llm_agent.infrastructure.news_reader import (
    _arrow_to_articles,
    _dedup_key,
    fetch_news,
)


def _make_arrow_table(rows: list[dict]) -> pa.Table:
    if not rows:
        schema = pa.schema([])
        return pa.table({}, schema=schema)
    keys = list(rows[0].keys())
    arrays = {k: pa.array([r.get(k) for r in rows]) for k in keys}
    return pa.table(arrays)


def _make_cfg(top_k: int = 8) -> Settings:
    return Settings(kafka_bootstrap_servers="localhost:9092", news_top_k=top_k)


def _mock_catalog_from_rows(rows: list[dict]) -> MagicMock:
    arrow_table = _make_arrow_table(rows)
    scan_mock = MagicMock()
    scan_mock.to_arrow.return_value = arrow_table
    table_mock = MagicMock()
    table_mock.scan.return_value = scan_mock
    catalog_mock = MagicMock()
    catalog_mock.load_table.return_value = table_mock
    return catalog_mock


class TestArrowToArticles:
    def test_maps_fresh_columns(self) -> None:
        table = _make_arrow_table(
            [{"title": "AAPL Up", "article_url": "http://x.com", "source_name": "Reuters", "published_at": "2026-06-01T10:00:00Z"}]
        )
        result = _arrow_to_articles(
            table,
            {"title": "title", "article_url": "url", "source_name": "source", "published_at": "published_at"},
        )
        assert len(result) == 1
        assert result[0]["title"] == "AAPL Up"
        assert result[0]["url"] == "http://x.com"
        assert result[0]["source"] == "Reuters"

    def test_missing_column_returns_none(self) -> None:
        table = _make_arrow_table([{"title": "Story"}])
        result = _arrow_to_articles(table, {"title": "title", "article_url": "url"})
        assert result[0]["url"] is None

    def test_empty_table(self) -> None:
        table = _make_arrow_table([])
        result = _arrow_to_articles(table, {"title": "title"})
        assert result == []


class TestDedupKey:
    def test_url_takes_priority(self) -> None:
        art = {"title": "Some Story", "url": "http://example.com/story"}
        assert _dedup_key(art) == "http://example.com/story"

    def test_md5_when_no_url(self) -> None:
        art: dict[str, str | None] = {"title": "Some Story", "url": None}
        expected = hashlib.md5("Some Story".encode()).hexdigest()
        assert _dedup_key(art) == expected

    def test_empty_url_falls_back_to_md5(self) -> None:
        art: dict[str, str | None] = {"title": "Story", "url": ""}
        expected = hashlib.md5("Story".encode()).hexdigest()
        assert _dedup_key(art) == expected


def _two_catalog_side_effect(fresh_rows: list[dict], hist_rows: list[dict]):
    call_count = {"n": 0}

    def side_effect(name: str, warehouse: str, cfg: object) -> MagicMock:
        if call_count["n"] == 0:
            call_count["n"] += 1
            return _mock_catalog_from_rows(fresh_rows)
        return _mock_catalog_from_rows(hist_rows)

    return side_effect


class TestFetchNews:
    @patch("llm_agent.infrastructure.news_reader._build_catalog")
    def test_union_from_both_catalogs(self, mock_build: MagicMock) -> None:
        fresh = [{"title": "Fresh Story", "article_url": "http://a.com", "source_name": "BBC", "published_at": "2026-06-01T14:00:00Z", "summary": ""}]
        hist = [{"title": "Old Story", "url": "http://b.com", "source_name": "CNN", "published_at": "2026-06-01T08:00:00Z", "description": ""}]
        mock_build.side_effect = _two_catalog_side_effect(fresh, hist)
        result = fetch_news("AAPL", _make_cfg())
        assert len(result) == 2

    @patch("llm_agent.infrastructure.news_reader._build_catalog")
    def test_dedup_same_url(self, mock_build: MagicMock) -> None:
        shared_url = "http://shared.com"
        fresh = [{"title": "Same Story", "article_url": shared_url, "source_name": "Reuters", "published_at": "2026-06-01T14:00:00Z", "summary": ""}]
        hist = [{"title": "Same Story", "url": shared_url, "source_name": "Reuters", "published_at": "2026-06-01T14:00:00Z", "description": ""}]
        mock_build.side_effect = _two_catalog_side_effect(fresh, hist)
        result = fetch_news("AAPL", _make_cfg())
        assert len(result) == 1

    @patch("llm_agent.infrastructure.news_reader._build_catalog")
    def test_dedup_same_title_no_url(self, mock_build: MagicMock) -> None:
        fresh = [{"title": "Duplicate Title", "article_url": None, "source_name": "X", "published_at": "2026-06-01T14:00:00Z", "summary": ""}]
        hist = [{"title": "Duplicate Title", "url": None, "source_name": "X", "published_at": "2026-06-01T14:00:00Z", "description": ""}]
        mock_build.side_effect = _two_catalog_side_effect(fresh, hist)
        result = fetch_news("AAPL", _make_cfg())
        assert len(result) == 1

    @patch("llm_agent.infrastructure.news_reader._build_catalog")
    def test_top_k_truncates(self, mock_build: MagicMock) -> None:
        fresh = [{"title": f"F{i}", "article_url": f"http://f{i}.com", "source_name": "S", "published_at": f"2026-06-01T{i:02d}:00:00Z", "summary": ""} for i in range(5)]
        hist = [{"title": f"H{i}", "url": f"http://h{i}.com", "source_name": "S", "published_at": f"2026-05-31T{i:02d}:00:00Z", "description": ""} for i in range(5)]
        mock_build.side_effect = _two_catalog_side_effect(fresh, hist)
        result = fetch_news("AAPL", _make_cfg(top_k=3))
        assert len(result) == 3

    @patch("llm_agent.infrastructure.news_reader._build_catalog")
    def test_bronze_failure_still_returns_silver(self, mock_build: MagicMock) -> None:
        hist = [{"title": "Hist Story", "url": "http://h.com", "source_name": "AP", "published_at": "2026-06-01T10:00:00Z", "description": ""}]
        call_count = {"n": 0}

        def side_effect(name: str, warehouse: str, cfg: object) -> MagicMock:
            if call_count["n"] == 0:
                call_count["n"] += 1
                raise ConnectionError("catalog unreachable")
            return _mock_catalog_from_rows(hist)

        mock_build.side_effect = side_effect
        result = fetch_news("AAPL", _make_cfg())
        assert len(result) == 1
        assert result[0]["title"] == "Hist Story"

    @patch("llm_agent.infrastructure.news_reader._build_catalog")
    def test_silver_failure_still_returns_fresh(self, mock_build: MagicMock) -> None:
        fresh = [{"title": "Fresh", "article_url": "http://f.com", "source_name": "BB", "published_at": "2026-06-01T14:00:00Z", "summary": ""}]
        call_count = {"n": 0}

        def side_effect(name: str, warehouse: str, cfg: object) -> MagicMock:
            if call_count["n"] == 0:
                call_count["n"] += 1
                return _mock_catalog_from_rows(fresh)
            raise ConnectionError("catalog unreachable")

        mock_build.side_effect = side_effect
        result = fetch_news("AAPL", _make_cfg())
        assert len(result) == 1

    @patch("llm_agent.infrastructure.news_reader._build_catalog")
    def test_both_catalogs_fail_returns_empty(self, mock_build: MagicMock) -> None:
        mock_build.side_effect = Exception("total failure")
        result = fetch_news("AAPL", _make_cfg())
        assert result == []

    @patch("llm_agent.infrastructure.news_reader._build_catalog")
    def test_sorted_newest_first(self, mock_build: MagicMock) -> None:
        fresh = [
            {"title": "Early", "article_url": "http://e.com", "source_name": "X", "published_at": "2026-06-01T08:00:00Z", "summary": ""},
            {"title": "Late", "article_url": "http://l.com", "source_name": "X", "published_at": "2026-06-01T14:00:00Z", "summary": ""},
        ]
        mock_build.side_effect = _two_catalog_side_effect(fresh, [])
        result = fetch_news("AAPL", _make_cfg())
        assert result[0]["title"] == "Late"
        assert result[1]["title"] == "Early"
