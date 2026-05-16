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
    """Tests for DELETE /api/v1/transactions/{id}."""

    def test_delete_existing_transaction_returns_204(self, monkeypatch):
        txn = _make_txn()
        posted = _make_posted()

        def mock_query(model):
            q = MagicMock()
            q.filter.return_value.first.return_value = (
                txn if "TransactionPending" in str(model) else posted
            )
            q.filter.return_value.delete.return_value = None
            return q

        with (
            patch("app.api.v1.transactions.get_current_user", return_value=MagicMock()),
            patch("app.api.v1.transactions.get_db"),
        ):
            db_mock = MagicMock()

            def query_side_effect(model):
                from app.models.database import (
                    TransactionPending,
                    TransactionPosted,
                    JournalEntryLine,
                )

                q = MagicMock()
                if model is TransactionPending:
                    q.filter.return_value.first.return_value = txn
                elif model is TransactionPosted:
                    q.filter.return_value.first.return_value = posted
                elif model is JournalEntryLine:
                    q.filter.return_value.delete.return_value = 0
                return q

            db_mock.query.side_effect = query_side_effect

            monkeypatch.setattr(
                "app.api.v1.transactions.get_db",
                lambda: iter([db_mock]),
            )
            monkeypatch.setattr(
                "app.api.v1.transactions.get_current_user",
                lambda: MagicMock(),
            )

        client = TestClient(app)
        with patch(
            "app.core.auth.get_current_user",
            return_value=MagicMock(id="u1", email="test@test.com"),
        ):
            # Use dependency override instead
            pass

        # Use app dependency overrides for clean testing
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep
        from app.models.database import (
            TransactionPending,
            TransactionPosted,
            JournalEntryLine,
        )

        txn_obj = _make_txn("txn-to-delete")
        posted_obj = _make_posted("posted-x")

        db = MagicMock()

        def query_dispatch(model):
            q = MagicMock()
            if model is TransactionPending:
                q.filter.return_value.first.return_value = txn_obj
            elif model is TransactionPosted:
                q.filter.return_value.first.return_value = posted_obj
            elif model is JournalEntryLine:
                q.filter.return_value.delete.return_value = 0
            return q

        db.query.side_effect = query_dispatch

        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="t@t.com")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.delete("/api/v1/transactions/txn-to-delete")
            assert response.status_code == 204
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

    def test_delete_cascades_journal_lines(self):
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep
        from app.models.database import (
            TransactionPending,
            TransactionPosted,
            JournalEntryLine,
        )

        txn_obj = _make_txn("txn-cascade")
        posted_obj = _make_posted("posted-cascade")
        deleted_lines = []

        db = MagicMock()

        journal_q = MagicMock()
        journal_q.filter.return_value.delete.side_effect = lambda **kw: (
            deleted_lines.append(1)
        )

        def query_dispatch(model):
            q = MagicMock()
            if model is TransactionPending:
                q.filter.return_value.first.return_value = txn_obj
            elif model is TransactionPosted:
                q.filter.return_value.first.return_value = posted_obj
            elif model is JournalEntryLine:
                return journal_q
            return q

        db.query.side_effect = query_dispatch

        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="t@t.com")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.delete("/api/v1/transactions/txn-cascade")
            assert response.status_code == 204
            assert len(deleted_lines) == 1
        finally:
            app.dependency_overrides.clear()


class TestDeleteTransactionsByIngest:
    """Tests for DELETE /api/v1/transactions/by-ingest/{ingest_id}."""

    def test_delete_by_ingest_returns_count(self, monkeypatch):
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep

        deleted_ids = []

        def mock_cascade(db, txn_id):
            deleted_ids.append(txn_id)

        monkeypatch.setattr(
            "app.api.v1.transactions._delete_transaction_cascade", mock_cascade
        )

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
            client = TestClient(app)
            response = client.delete("/api/v1/transactions/by-ingest/ingest-x")
            assert response.status_code == 200
            assert response.json()["deleted"] == 2
            assert deleted_ids == ["txn-a", "txn-b"]
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
