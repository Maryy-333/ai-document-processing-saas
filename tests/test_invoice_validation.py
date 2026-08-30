from decimal import Decimal

import pytest

from app.processing.invoice_validation import (
    ARITHMETIC_TOLERANCE,
    ValidationSeverity,
    validate_invoice,
)
from app.schemas.invoice_extraction import ExtractedInvoice, ExtractedLineItem

COMPLETE_VALID_INVOICE = ExtractedInvoice(
    vendor_name="ABC Supplies Ltd",
    vendor_address="123 Main Street",
    customer_name="Example Company",
    invoice_number="INV-1001",
    invoice_date="2026-08-20",
    due_date="2026-09-20",
    currency="USD",
    subtotal=Decimal("100.00"),
    tax=Decimal("15.00"),
    total=Decimal("115.00"),
    payment_terms="Net 30",
    line_items=[
        ExtractedLineItem(
            description="Office chairs",
            quantity=Decimal("5"),
            unit_price=Decimal("20.00"),
            amount=Decimal("100.00"),
        )
    ],
)


def test_completely_valid_invoice_has_no_findings():
    result = validate_invoice(COMPLETE_VALID_INVOICE)
    assert result.is_valid is True
    assert result.errors == []
    assert result.warnings == []
    assert result.requires_review is False


def test_missing_optional_field_produces_warning_not_error():
    invoice = COMPLETE_VALID_INVOICE.model_copy(update={"payment_terms": None})
    result = validate_invoice(invoice)
    assert result.is_valid is True  # warnings don't affect is_valid
    assert len(result.warnings) == 1
    assert result.warnings[0].field == "payment_terms"
    assert result.warnings[0].severity == ValidationSeverity.WARNING
    assert result.warnings[0].code == "FIELD_MISSING"
    assert result.requires_review is True


def test_all_fields_missing_produces_warning_per_field_plus_no_line_items():
    empty = ExtractedInvoice()
    result = validate_invoice(empty)
    assert result.is_valid is True  # missing data alone is never an error
    # 11 top-level fields + the empty-line-items warning
    assert len(result.warnings) == 12
    assert all(w.severity == ValidationSeverity.WARNING for w in result.warnings)
    codes = {w.code for w in result.warnings}
    assert codes == {"FIELD_MISSING", "NO_LINE_ITEMS"}


def test_subtotal_plus_tax_equals_total_no_error():
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={"subtotal": Decimal("100.00"), "tax": Decimal("15.00"), "total": Decimal("115.00")}
    )
    result = validate_invoice(invoice)
    assert not any(e.code == "TOTAL_MISMATCH" for e in result.errors)


def test_subtotal_plus_tax_not_equal_total_produces_error():
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={"subtotal": Decimal("100.00"), "tax": Decimal("20.00"), "total": Decimal("500.00")}
    )
    result = validate_invoice(invoice)
    assert result.is_valid is False
    matching = [e for e in result.errors if e.code == "TOTAL_MISMATCH"]
    assert len(matching) == 1
    assert matching[0].field == "total"
    assert matching[0].severity == ValidationSeverity.ERROR


def test_missing_subtotal_skips_total_check_without_extra_error():
    invoice = COMPLETE_VALID_INVOICE.model_copy(update={"subtotal": None})
    result = validate_invoice(invoice)
    assert not any(e.code == "TOTAL_MISMATCH" for e in result.errors)
    # The missing subtotal itself is a warning, not an error.
    assert any(w.field == "subtotal" for w in result.warnings)


def test_missing_tax_skips_total_check():
    invoice = COMPLETE_VALID_INVOICE.model_copy(update={"tax": None})
    result = validate_invoice(invoice)
    assert not any(e.code == "TOTAL_MISMATCH" for e in result.errors)


def test_missing_total_skips_total_check():
    invoice = COMPLETE_VALID_INVOICE.model_copy(update={"total": None})
    result = validate_invoice(invoice)
    assert not any(e.code == "TOTAL_MISMATCH" for e in result.errors)


def test_line_item_arithmetic_correct_no_error():
    result = validate_invoice(COMPLETE_VALID_INVOICE)
    assert not any(e.code == "LINE_ITEM_AMOUNT_MISMATCH" for e in result.errors)


def test_line_item_arithmetic_mismatch_produces_error():
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={
            "line_items": [
                ExtractedLineItem(
                    description="Office chairs",
                    quantity=Decimal("5"),
                    unit_price=Decimal("20.00"),
                    amount=Decimal("999.00"),
                )
            ]
        }
    )
    result = validate_invoice(invoice)
    assert result.is_valid is False
    matching = [e for e in result.errors if e.code == "LINE_ITEM_AMOUNT_MISMATCH"]
    assert len(matching) == 1
    assert matching[0].field == "line_items[0].amount"


def test_missing_line_item_values_skips_arithmetic_check():
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={
            "line_items": [
                ExtractedLineItem(
                    description="Shipping", quantity=None, unit_price=None, amount=None
                )
            ]
        }
    )
    result = validate_invoice(invoice)
    assert not any(e.code == "LINE_ITEM_AMOUNT_MISMATCH" for e in result.errors)
    assert result.is_valid is True


def test_multiple_line_items_each_checked_independently():
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={
            "line_items": [
                ExtractedLineItem(
                    description="Correct",
                    quantity=Decimal("2"),
                    unit_price=Decimal("10.00"),
                    amount=Decimal("20.00"),
                ),
                ExtractedLineItem(
                    description="Wrong",
                    quantity=Decimal("3"),
                    unit_price=Decimal("10.00"),
                    amount=Decimal("999.00"),
                ),
                ExtractedLineItem(
                    description="Also correct",
                    quantity=Decimal("1"),
                    unit_price=Decimal("5.00"),
                    amount=Decimal("5.00"),
                ),
            ]
        }
    )
    result = validate_invoice(invoice)
    matching = [e for e in result.errors if e.code == "LINE_ITEM_AMOUNT_MISMATCH"]
    assert len(matching) == 1
    assert matching[0].field == "line_items[1].amount"


def test_decimal_precision_used_not_float():
    # 0.1 + 0.2 != 0.3 in float arithmetic — this must not falsely trigger
    # with Decimal-safe comparison.
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={"subtotal": Decimal("0.10"), "tax": Decimal("0.20"), "total": Decimal("0.30")}
    )
    result = validate_invoice(invoice)
    assert not any(e.code == "TOTAL_MISMATCH" for e in result.errors)


def test_value_exactly_at_tolerance_boundary_does_not_error():
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={
            "subtotal": Decimal("100.00"),
            "tax": Decimal("15.00"),
            "total": Decimal("115.00") + ARITHMETIC_TOLERANCE,
        }
    )
    result = validate_invoice(invoice)
    assert not any(e.code == "TOTAL_MISMATCH" for e in result.errors)


def test_value_just_outside_tolerance_does_error():
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={
            "subtotal": Decimal("100.00"),
            "tax": Decimal("15.00"),
            "total": Decimal("115.00") + ARITHMETIC_TOLERANCE + Decimal("0.01"),
        }
    )
    result = validate_invoice(invoice)
    assert any(e.code == "TOTAL_MISMATCH" for e in result.errors)


def test_multiple_simultaneous_findings():
    invoice = ExtractedInvoice(
        vendor_name="Acme",
        subtotal=Decimal("100.00"),
        tax=Decimal("10.00"),
        total=Decimal("999.00"),  # mismatch
        line_items=[
            ExtractedLineItem(
                description="Bad item",
                quantity=Decimal("1"),
                unit_price=Decimal("1.00"),
                amount=Decimal("50.00"),  # mismatch
            )
        ],
    )
    result = validate_invoice(invoice)
    assert result.is_valid is False
    assert len(result.errors) == 2
    assert len(result.warnings) >= 1  # several fields still missing
    assert result.requires_review is True


def test_warnings_and_errors_are_distinct_lists():
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={"due_date": None, "total": Decimal("999999.00")}
    )
    result = validate_invoice(invoice)
    assert any(w.code == "FIELD_MISSING" for w in result.warnings)
    assert any(e.code == "TOTAL_MISMATCH" for e in result.errors)
    assert all(w.severity == ValidationSeverity.WARNING for w in result.warnings)
    assert all(e.severity == ValidationSeverity.ERROR for e in result.errors)


def test_validation_does_not_mutate_invoice_data():
    original = COMPLETE_VALID_INVOICE.model_copy(deep=True)
    invoice = COMPLETE_VALID_INVOICE.model_copy(
        update={"total": Decimal("999.00")}, deep=True
    )
    before = invoice.model_dump()
    validate_invoice(invoice)
    after = invoice.model_dump()
    assert before == after
    # Sanity: confirm the mutated-total copy really differs from the
    # original fixture, proving this test isn't vacuous.
    assert invoice.total != original.total


def test_none_values_remain_none_after_validation():
    invoice = ExtractedInvoice()
    validate_invoice(invoice)
    assert invoice.vendor_name is None
    assert invoice.total is None
    assert invoice.line_items == []


def test_validation_is_deterministic():
    results = [validate_invoice(COMPLETE_VALID_INVOICE) for _ in range(5)]
    assert all(r.model_dump() == results[0].model_dump() for r in results)


@pytest.mark.parametrize(
    "mismatch_invoice_kwargs",
    [
        {"subtotal": Decimal("50.00"), "tax": Decimal("5.00"), "total": Decimal("100.00")},
        {"subtotal": Decimal("0.00"), "tax": Decimal("0.00"), "total": Decimal("1.00")},
    ],
)
def test_various_total_mismatches_detected(mismatch_invoice_kwargs):
    invoice = COMPLETE_VALID_INVOICE.model_copy(update=mismatch_invoice_kwargs)
    result = validate_invoice(invoice)
    assert any(e.code == "TOTAL_MISMATCH" for e in result.errors)
