"""Tests for PATCH /api/v1/transactions/{id}/fecha (HITL fecha resolution)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from fastapi.testclient import TestClient
from main import app


def _make_pending_txn(
    txn_id: str = "txn-001",
    status_value: str = "pending",
    raw_data: dict | None = None,
):
    """Build a stand-in TransactionPending whose attributes match the ORM model."""
    from app.models.database import TransactionStatus

    txn = MagicMock()
    txn.id = txn_id
    # `status` is compared with `TransactionStatus.POSTED` in the handler, so we
    # need a real enum value, not a stringified mock.
    txn.status = (
        TransactionStatus.POSTED
        if status_value == "posted"
        else TransactionStatus.PENDING
    )
    txn.raw_data = raw_data or {"needs_user_fecha": True, "fecha": None}
    txn.fecha = None
    return txn


class TestSetTransactionFecha:
    """Tests for PATCH /transactions/{id}/fecha."""

    def test_sets_fecha_for_pending_transaction(self):
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep
        from app.models.database import TransactionPending

        txn = _make_pending_txn("txn-1", status_value="pending")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = txn

        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="a@b.c")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.patch(
                "/api/v1/transactions/txn-1/fecha",
                json={"fecha": "2026-01-06"},
            )
            assert response.status_code == 200
            # The handler should have written the parsed datetime onto the txn
            # and cleared the needs_user_fecha flag.
            assert txn.fecha == datetime(2026, 1, 6, 0, 0, 0, tzinfo=timezone.utc)
            assert "needs_user_fecha" not in (txn.raw_data or {})
            assert txn.raw_data["fecha"] == "2026-01-06"
            db.commit.assert_called()
        finally:
            app.dependency_overrides.clear()
            _ = TransactionPending  # ensure import side-effects run if any

    def test_rejects_posted_transaction_with_409(self):
        """Once a transaction is POSTED, its fecha is mirrored in journal lines
        and derived statements — patching here would silently desync them.
        """
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep

        txn = _make_pending_txn("txn-posted", status_value="posted")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = txn

        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="a@b.c")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.patch(
                "/api/v1/transactions/txn-posted/fecha",
                json={"fecha": "2026-01-06"},
            )
            assert response.status_code == 409
            assert "contabilizada" in response.json()["detail"].lower()
            # Must not have touched the transaction or committed anything.
            assert txn.fecha is None
            db.commit.assert_not_called()
        finally:
            app.dependency_overrides.clear()

    def test_returns_404_when_transaction_missing(self):
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="a@b.c")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.patch(
                "/api/v1/transactions/missing/fecha",
                json={"fecha": "2026-01-06"},
            )
            assert response.status_code == 404
        finally:
            app.dependency_overrides.clear()

    def test_returns_422_for_unparseable_fecha(self):
        from app.core.auth import get_current_user as auth_dep
        from app.core.database import get_db as db_dep

        txn = _make_pending_txn("txn-bad-date", status_value="pending")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = txn

        app.dependency_overrides[auth_dep] = lambda: MagicMock(id="u1", email="a@b.c")
        app.dependency_overrides[db_dep] = lambda: db

        try:
            client = TestClient(app)
            response = client.patch(
                "/api/v1/transactions/txn-bad-date/fecha",
                json={"fecha": "not-a-real-date"},
            )
            assert response.status_code == 422
        finally:
            app.dependency_overrides.clear()
