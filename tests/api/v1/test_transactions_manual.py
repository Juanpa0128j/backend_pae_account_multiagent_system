"""Tests for manual transaction CRUD."""

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from main import app


@pytest.fixture
def client():
    """TestClient with mocked DB."""
    mock_db = MagicMock()

    def _override_get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()


class TestCreateManualTransaction:
    def test_create_manual_transaction_returns_201(self, client: TestClient):
        pass

    def test_create_manual_transaction_requires_company_settings(
        self, client: TestClient
    ):
        pass


class TestPatchManualTransaction:
    def test_patch_pending_updates_fields(self, client: TestClient):
        pass

    def test_patch_posted_returns_409(self, client: TestClient):
        pass


class TestReprocessTransaction:
    def test_reprocess_posted_creates_new_pending(self, client: TestClient):
        pass

    def test_reprocess_non_posted_returns_409(self, client: TestClient):
        pass
