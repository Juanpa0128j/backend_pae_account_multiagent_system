"""Tests for transaction DELETE endpoints."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from main import app


def _make_txn(txn_id: str = "txn-001", ingest_id: str = "ingest-001"):
    txn = MagicMock()
    txn.id = txn_id
    txn.ingest_id = ingest_id
    txn.status = MagicMock(value="PENDING")
    return txn


def _make_posted(posted_id: str = "posted-001"):
    posted = MagicMock()
    posted.id = posted_id
    return posted


class TestDeleteTransaction:
    """Tests for DELETE /api/v1/transactions/{id} (cascade delete by pending id)."""

    def test_delete_existing_transaction_returns_204(self):
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep
        from app.models.database import TransactionPending, TransactionPosted

        db = MagicMock()
        txn = MagicMock()
        txn.company_nit = None  # keeps _resync_derived_statements a no-op

        def query_dispatch(model):
            q = MagicMock()
            if model is TransactionPending:
                q.filter.return_value.first.return_value = txn
            elif model is TransactionPosted:
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = query_dispatch
        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="t@t.com")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.delete("/api/v1/transactions/txn-to-delete")
            assert response.status_code == 204
            db.delete.assert_called_with(txn)  # pending row removed by cascade
        finally:
            app.dependency_overrides.clear()

    def test_delete_nonexistent_transaction_returns_404(self):
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep
        from app.models.database import TransactionPending

        db = MagicMock()

        def query_dispatch(model):
            q = MagicMock()
            if model is TransactionPending:
                q.filter.return_value.first.return_value = None
            return q

        db.query.side_effect = query_dispatch
        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="t@t.com")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.delete("/api/v1/transactions/does-not-exist")
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_delete_cascades_posted_and_journal_lines(self):
        """Cascade removes the pending row plus its posted children + journal lines."""
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep
        from app.models.database import (
            JournalEntryLine,
            TransactionPending,
            TransactionPosted,
        )

        db = MagicMock()
        txn = MagicMock()
        txn.company_nit = None
        posted = MagicMock()
        posted.id = "posted-1"
        posted.company_nit = None

        def query_dispatch(model):
            q = MagicMock()
            if model is TransactionPending:
                q.filter.return_value.first.return_value = txn
            elif model is TransactionPosted:
                q.filter.return_value.all.return_value = [posted]
            elif model is JournalEntryLine:
                q.filter.return_value.delete.return_value = 1
            return q

        db.query.side_effect = query_dispatch
        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="t@t.com")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.delete("/api/v1/transactions/txn-cascade")
            assert response.status_code == 204
            db.delete.assert_any_call(posted)  # posted child removed
            db.delete.assert_any_call(txn)  # pending removed
        finally:
            app.dependency_overrides.clear()


class TestDeleteTransactionsByIngest:
    """Tests for DELETE /api/v1/transactions/by-ingest/{ingest_id}."""

    def test_delete_by_ingest_returns_count(self):
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep

        db = MagicMock()
        row1, row2 = MagicMock(), MagicMock()
        row1.id = "txn-a"
        row2.id = "txn-b"

        q = MagicMock()
        q.filter.return_value.all.return_value = [row1, row2]
        db.query.return_value = q

        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="t@t.com")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            with patch(
                "app.api.v1.transactions.db_service.soft_delete_transaction_posted",
                return_value=True,
            ):
                client = TestClient(app)
                response = client.delete("/api/v1/transactions/by-ingest/ingest-x")
                assert response.status_code == 200
                assert response.json()["deleted"] == 2
        finally:
            app.dependency_overrides.clear()

    def test_delete_by_ingest_not_found_returns_404(self):
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep
        from app.models.database import TransactionPending

        db = MagicMock()

        def query_dispatch(model):
            q = MagicMock()
            if model is TransactionPending:
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = query_dispatch

        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="t@t.com")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.delete(
                "/api/v1/transactions/by-ingest/nonexistent-ingest"
            )
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()
