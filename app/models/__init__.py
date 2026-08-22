"""
Import every model module so Base.metadata is fully populated wherever
app.models is imported — required for Alembic autogenerate to see all tables,
and convenient for application code that just needs the ORM classes.
"""

from app.models.document import Document
from app.models.invoice import Invoice
from app.models.invoice_line_item import InvoiceLineItem
from app.models.organization import Organization
from app.models.processing_job import ProcessingJob
from app.models.user import User

__all__ = [
    "Document",
    "Invoice",
    "InvoiceLineItem",
    "Organization",
    "ProcessingJob",
    "User",
]
