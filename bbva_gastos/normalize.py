from datetime import datetime, timedelta, timezone
from decimal import Decimal


def pick_aspsp(aspsps, query):
    needle = query.casefold()
    matches = [bank for bank in aspsps if needle in bank.get("name", "").casefold()]
    exact = [bank for bank in matches if bank.get("name", "").casefold() == needle]
    if len(exact) == 1:
        return exact[0]
    if len(matches) == 1:
        return matches[0]
    names = ", ".join(sorted(bank.get("name", "") for bank in matches)) or "(ninguno)"
    raise ValueError(
        f"Hay {len(matches)} bancos que coinciden con {query!r}: {names}. "
        "Pasa el nombre exacto con --aspsp."
    )


def consent_valid_until(maximum_seconds, now=None):
    if maximum_seconds < 3600:
        raise ValueError("maximum_consent_validity demasiado corto")
    moment = now or datetime.now(timezone.utc)
    until = moment + timedelta(seconds=maximum_seconds - 3600)
    return until.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00")


def signed_amount(transaction):
    money = transaction.get("transaction_amount") or {}
    amount = Decimal(str(money.get("amount", "0")))
    indicator = transaction.get("credit_debit_indicator")
    if indicator == "DBIT":
        return -abs(amount)
    if indicator == "CRDT":
        return abs(amount)
    return amount


def describe(transaction):
    lines = [line.strip() for line in transaction.get("remittance_information") or [] if line and line.strip()]
    if lines:
        return " | ".join(lines)
    note = (transaction.get("note") or "").strip()
    if note and note != "string":
        return note
    code = transaction.get("bank_transaction_code") or {}
    if code.get("description"):
        return code["description"]
    for party in ("creditor", "debtor"):
        name = ((transaction.get(party) or {}).get("name") or "").strip()
        if name and name != "string":
            return name
    return ""


def normalize_transaction(transaction, account):
    money = transaction.get("transaction_amount") or {}
    transaction_id = transaction.get("transaction_id") or ""
    if transaction_id == "string":
        transaction_id = ""
    return {
        "id": transaction_id or transaction.get("entry_reference") or "",
        "account_uid": account.get("uid"),
        "account_name": account.get("name") or account.get("product") or "",
        "iban_last4": _iban_last4(account),
        "booking_date": transaction.get("booking_date"),
        "value_date": transaction.get("value_date"),
        "amount": format(signed_amount(transaction), "f"),
        "currency": money.get("currency") or account.get("currency") or "EUR",
        "description": describe(transaction),
        "status": transaction.get("status"),
        "creditor": ((transaction.get("creditor") or {}).get("name") or ""),
        "debtor": ((transaction.get("debtor") or {}).get("name") or ""),
    }


def to_canonical(row):
    """Pasa una fila de Enable Banking al mismo esquema que el extracto."""
    amount = Decimal(str(row["amount"]))
    parts = [part.strip() for part in (row.get("description") or "").split("|") if part.strip()]
    return {
        "id": row.get("id") or "",
        "fecha": row.get("booking_date") or "",
        "fecha_valor": row.get("value_date") or "",
        "concepto": parts[0] if parts else "Movimiento",
        "detalle": " | ".join(parts[1:]),
        "importe": format(amount, "f"),
        "saldo": "",
        "divisa": row.get("currency") or "EUR",
        "direccion": "ingreso" if amount >= 0 else "gasto",
        "lector": "enable-banking",
        "fichero": row.get("iban_last4") or "",
    }


def _iban_last4(account):
    iban = ((account.get("account_id") or {}).get("iban") or "").replace(" ", "")
    if len(iban) < 4:
        return ""
    return iban[-4:]
