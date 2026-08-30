from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.invoice_extraction import ExtractedInvoice, ExtractedLineItem


def test_valid_invoice_data_parses_correctly():
    invoice = ExtractedInvoice.model_validate(
        {
            "vendor_name": "ABC Supplies Ltd",
            "vendor_address": "123 Main Street",
            "customer_name": "Example Company",
            "invoice_number": "INV-1001",
            "invoice_date": "2026-08-20",
            "due_date": "2026-09-20",
            "currency": "USD",
            "subtotal": "100.00",
            "tax": "15.00",
            "total": "115.00",
            "payment_terms": "Net 30",
            "line_items": [
                {
                    "description": "Office chairs",
                    "quantity": 5,
                    "unit_price": "20.00",
                    "amount": "100.00",
                }
            ],
        }
    )
    assert invoice.vendor_name == "ABC Supplies Ltd"
    assert invoice.invoice_number == "INV-1001"
    assert invoice.invoice_date == date(2026, 8, 20)
    assert invoice.due_date == date(2026, 9, 20)
    assert invoice.subtotal == Decimal("100.00")
    assert len(invoice.line_items) == 1
    assert invoice.line_items[0].description == "Office chairs"


def test_missing_fields_become_none():
    invoice = ExtractedInvoice.model_validate({})
    assert invoice.vendor_name is None
    assert invoice.vendor_address is None
    assert invoice.customer_name is None
    assert invoice.invoice_number is None
    assert invoice.invoice_date is None
    assert invoice.due_date is None
    assert invoice.currency is None
    assert invoice.subtotal is None
    assert invoice.tax is None
    assert invoice.total is None
    assert invoice.payment_terms is None
    assert invoice.line_items == []


def test_explicit_nulls_are_preserved_as_none():
    invoice = ExtractedInvoice.model_validate(
        {"vendor_name": "Acme", "invoice_number": None, "due_date": None}
    )
    assert invoice.vendor_name == "Acme"
    assert invoice.invoice_number is None
    assert invoice.due_date is None


def test_multiple_line_items_parsed_correctly():
    invoice = ExtractedInvoice.model_validate(
        {
            "line_items": [
                {
                    "description": "Widget A",
                    "quantity": 2,
                    "unit_price": "10.00",
                    "amount": "20.00",
                },
                {
                    "description": "Widget B",
                    "quantity": 1,
                    "unit_price": "5.50",
                    "amount": "5.50",
                },
                {
                    "description": "Widget C",
                    "quantity": 3,
                    "unit_price": "2.25",
                    "amount": "6.75",
                },
            ]
        }
    )
    assert len(invoice.line_items) == 3
    assert [li.description for li in invoice.line_items] == ["Widget A", "Widget B", "Widget C"]
    assert invoice.line_items[2].amount == Decimal("6.75")


def test_decimal_precision_preserved():
    invoice = ExtractedInvoice.model_validate({"subtotal": "1234.56", "tax": "98.76"})
    assert invoice.subtotal == Decimal("1234.56")
    assert invoice.tax == Decimal("98.76")
    # Precision must not degrade through a float round-trip.
    assert str(invoice.subtotal) == "1234.56"


def test_invoice_number_formatting_preserved_exactly():
    invoice = ExtractedInvoice.model_validate({"invoice_number": "INV-2026-00042"})
    assert invoice.invoice_number == "INV-2026-00042"


def test_invalid_date_format_raises_validation_error():
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate({"invoice_date": "not-a-date"})


def test_invalid_decimal_value_raises_validation_error():
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate({"subtotal": "definitely-not-a-number"})


def test_line_items_must_be_a_list():
    with pytest.raises(ValidationError):
        ExtractedInvoice.model_validate({"line_items": "not a list"})


def test_line_item_can_have_all_null_fields():
    item = ExtractedLineItem.model_validate({})
    assert item.description is None
    assert item.quantity is None
    assert item.unit_price is None
    assert item.amount is None
