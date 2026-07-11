"""Tests for ``Container.require_delivery()``.

Replaces a bare ``assert delivery is not None`` (stripped under
``python -O`` / ``PYTHONOPTIMIZE=1``) with an explicit, always-enforced check.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from alert_service.container import Container


def _container(delivery: object | None = None) -> Container:
    return Container(
        history_writer=MagicMock(),
        judgement_writer=MagicMock(),
        delivery=delivery,  # type: ignore[arg-type]
    )


def test_require_delivery_returns_delivery_when_set() -> None:
    delivery = MagicMock()
    container = _container(delivery=delivery)

    assert container.require_delivery() is delivery


def test_require_delivery_raises_runtime_error_when_none() -> None:
    container = _container(delivery=None)

    with pytest.raises(RuntimeError, match="lifespan\\(\\) must run"):
        container.require_delivery()
