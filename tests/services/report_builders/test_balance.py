from unittest.mock import MagicMock

from app.services.report_builders.balance import build_balance


def _make_svc(balance_data=None, ledger=None):
    svc = MagicMock()
    svc.get_balance_sheet.return_value = balance_data or {
        "activos": [],
        "pasivos": [],
        "patrimonio": [],
        "assets": 0,
        "liabilities": 0,
        "equity": 0,
        "net_profit": 0,
        "total_equity": 0,
        "is_balanced": True,
    }
    svc.get_general_ledger.return_value = ledger or []
    return svc


def test_build_balance_returns_dict():
    result = build_balance(MagicMock(), {}, _make_svc())
    assert isinstance(result, dict)


def test_build_balance_has_report_type():
    result = build_balance(MagicMock(), {}, _make_svc())
    assert result.get("report_type") == "balance_sheet"


def test_build_balance_calls_get_balance_sheet():
    svc = _make_svc()
    build_balance(MagicMock(), {}, svc)
    svc.get_balance_sheet.assert_called_once()


def test_build_balance_passes_company_nit():
    svc = _make_svc()
    build_balance(MagicMock(), {"company_nit": "800999888"}, svc)
    call_kwargs = svc.get_balance_sheet.call_args
    assert "800999888" in str(call_kwargs)


def _reclass_svc():
    """Ledger where 130505 nets CREDIT — reclassified into pasivos_detalle."""
    return _make_svc(
        balance_data={
            "assets": 500000.0,
            "liabilities": 500000.0,
            "equity": 0,
            "net_profit": 0,
            "total_equity": 0,
            "is_balanced": True,
        },
        ledger=[
            {
                "account": "111005",
                "name": "Bancos",
                "total_debit": 500000,
                "total_credit": 0,
            },
            {
                "account": "130505",
                "name": "Clientes nacionales",
                "total_debit": 0,
                "total_credit": 500000,
            },
        ],
    )


def test_build_balance_reclassified_row_annotated_as_anticipo():
    result = build_balance(MagicMock(), {}, _reclass_svc())
    codigos_activos = [c["codigo"] for c in result["activos_detalle"]]
    assert "130505" not in codigos_activos
    row = next(c for c in result["pasivos_detalle"] if c["codigo"] == "130505")
    assert row["nombre"] == "Clientes nacionales (anticipo de cliente — reclasificada)"
    assert row["saldo"] == 500000.0


def test_build_balance_reclass_note_appended_to_mensaje():
    result = build_balance(MagicMock(), {}, _reclass_svc())
    assert result["mensaje_cuadre"].endswith(
        " | Nota: cuenta(s) 130505 presentada(s) en pasivos como anticipo de "
        "cliente (saldo acreedor). Posible factura de origen sin contabilizar."
    )


def test_build_balance_no_reclass_no_note():
    result = build_balance(MagicMock(), {}, _make_svc())
    assert "Nota:" not in result["mensaje_cuadre"]
