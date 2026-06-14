"""Unit tests for DIAN field help texts loaded from static JSON."""

from app.services.tax_declaration_service import _HELP_TEXTS, DraftField


def test_known_renglon_42_has_correct_help_text():
    # Arrange
    expected = "Ingresos brutos operacionales del período fiscal"

    # Act
    field = DraftField(
        renglon="42",
        label="Test",
        value=0.0,
        source="test",
        confidence="high",
        requires_review=False,
        help_text=_HELP_TEXTS.get("42"),
    )

    # Assert
    assert field.help_text == expected


def test_unknown_renglon_yields_none_help_text():
    # Arrange / Act
    field = DraftField(
        renglon="9999",
        label="Unknown",
        value=0.0,
        source="test",
        confidence="low",
        requires_review=True,
        help_text=_HELP_TEXTS.get("9999"),
    )

    # Assert
    assert field.help_text is None


def test_help_texts_map_loads_all_required_renglones():
    required = {
        "42",
        "43",
        "44",
        "45",
        "47",
        "48",
        "51",
        "52",
        "59",
        "63",
        "64",
        "76",
        "88",
        "89",
        "96",
        "97",
        "98",
        "99",
    }
    assert required.issubset(_HELP_TEXTS.keys())


def test_help_texts_values_are_non_empty_strings():
    for code, text in _HELP_TEXTS.items():
        assert isinstance(text, str) and len(text) > 0, (
            f"Empty help text for renglon {code}"
        )
