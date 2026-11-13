from datetime import datetime, timezone
from decimal import Decimal

import pytest

from bbva_gastos.normalize import (
    consent_valid_until,
    describe,
    normalize_transaction,
    pick_aspsp,
    signed_amount,
)


def test_pick_aspsp_prefers_exact_name():
    banks = [
        {"name": "BBVA", "country": "ES"},
        {"name": "BBVA Consumer Finance", "country": "ES"},
    ]
    assert pick_aspsp(banks, "BBVA")["name"] == "BBVA"


def test_pick_aspsp_rejects_several_partial_matches():
    banks = [{"name": "BBVA Uno"}, {"name": "BBVA Dos"}]
    with pytest.raises(ValueError):
        pick_aspsp(banks, "bbva")


def test_consent_stays_under_the_bank_maximum():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    until = consent_valid_until(90 * 24 * 3600, now=now)
    assert until == "2026-03-31T23:00:00.000000+00:00"


def test_signed_amount_and_description():
    spent = {
        "credit_debit_indicator": "DBIT",
        "transaction_amount": {"amount": "12.50", "currency": "EUR"},
        "remittance_information": ["MERCADONA"],
    }
    received = {
        "credit_debit_indicator": "CRDT",
        "transaction_amount": {"amount": "1800.00", "currency": "EUR"},
        "remittance_information": [],
        "note": "NOMINA",
    }
    assert signed_amount(spent) == Decimal("-12.50")
    assert signed_amount(received) == Decimal("1800.00")
    assert describe(spent) == "MERCADONA"
    assert describe(received) == "NOMINA"


def test_normalize_keeps_a_stable_row():
    row = normalize_transaction(
        {
            "entry_reference": "abc",
            "credit_debit_indicator": "DBIT",
            "transaction_amount": {"amount": "3.00", "currency": "EUR"},
            "booking_date": "2026-11-01",
            "value_date": "2026-11-02",
            "status": "BOOK",
            "remittance_information": ["CAFETERIA"],
            "creditor": {"name": "Cafe"},
        },
        {"uid": "acc-1", "name": "Cuenta", "account_id": {"iban": "ES7921000813610123456789"}},
    )
    assert row["id"] == "abc"
    assert row["amount"] == "-3.00"
    assert row["iban_last4"] == "6789"
    assert row["description"] == "CAFETERIA"
