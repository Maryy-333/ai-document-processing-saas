import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Document, Organization, User
from app.models.enums import DocumentStatus


def make_org(session, name="Acme Bookkeeping") -> Organization:
    org = Organization(name=name)
    session.add(org)
    session.flush()
    return org


def make_user(session, org: Organization, email="user@example.com") -> User:
    user = User(organization_id=org.id, email=email, hashed_password="x")
    session.add(user)
    session.flush()
    return user


def make_document(session, org: Organization, user: User, storage_key: str) -> Document:
    doc = Document(
        organization_id=org.id,
        uploaded_by_user_id=user.id,
        original_filename="invoice.pdf",
        storage_key=storage_key,
        content_type="application/pdf",
        file_size_bytes=12345,
    )
    session.add(doc)
    session.flush()
    return doc


def test_document_defaults_to_uploaded_status(db_session):
    org = make_org(db_session)
    user = make_user(db_session, org)
    doc = make_document(db_session, org, user, "orgs/1/doc1.pdf")
    db_session.commit()

    db_session.refresh(doc)
    assert doc.status == DocumentStatus.UPLOADED


def test_document_requires_organization_id(db_session):
    org = make_org(db_session)
    user = make_user(db_session, org)
    doc = Document(
        organization_id=None,
        uploaded_by_user_id=user.id,
        original_filename="invoice.pdf",
        storage_key="orgs/none/doc.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
    )
    db_session.add(doc)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_organization_has_many_documents(db_session):
    org = make_org(db_session)
    user = make_user(db_session, org)
    make_document(db_session, org, user, "orgs/1/a.pdf")
    make_document(db_session, org, user, "orgs/1/b.pdf")
    db_session.commit()

    db_session.refresh(org)
    assert len(org.documents) == 2


def test_documents_are_isolated_by_organization(db_session):
    """
    Simulates the query shape that authorization must use once auth exists:
    filtering Document by organization_id. This test doesn't exercise an auth
    layer (none exists yet) — it verifies the schema actually supports that
    filter correctly, i.e. org A's documents never appear in org B's query.
    """
    org_a = make_org(db_session, "Org A")
    org_b = make_org(db_session, "Org B")
    user_a = make_user(db_session, org_a, "a@example.com")
    user_b = make_user(db_session, org_b, "b@example.com")

    make_document(db_session, org_a, user_a, "orgs/a/1.pdf")
    make_document(db_session, org_a, user_a, "orgs/a/2.pdf")
    make_document(db_session, org_b, user_b, "orgs/b/1.pdf")
    db_session.commit()

    org_a_docs = (
        db_session.query(Document).filter(Document.organization_id == org_a.id).all()
    )
    org_b_docs = (
        db_session.query(Document).filter(Document.organization_id == org_b.id).all()
    )

    assert len(org_a_docs) == 2
    assert len(org_b_docs) == 1
    assert all(d.organization_id == org_a.id for d in org_a_docs)
    assert not set(d.id for d in org_a_docs) & set(d.id for d in org_b_docs)


def test_storage_key_must_be_unique(db_session):
    org = make_org(db_session)
    user = make_user(db_session, org)
    make_document(db_session, org, user, "orgs/1/dup.pdf")
    db_session.flush()

    dup = Document(
        organization_id=org.id,
        uploaded_by_user_id=user.id,
        original_filename="other.pdf",
        storage_key="orgs/1/dup.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
    )
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_deleting_document_cascades_processing_jobs_and_invoice(db_session):
    from app.models import Invoice, ProcessingJob
    from app.models.enums import ProcessingStage

    org = make_org(db_session)
    user = make_user(db_session, org)
    doc = make_document(db_session, org, user, "orgs/1/cascade.pdf")
    db_session.add(Invoice(document_id=doc.id))
    db_session.add(ProcessingJob(document_id=doc.id, stage=ProcessingStage.VALIDATION))
    db_session.commit()

    db_session.delete(doc)
    db_session.commit()

    assert db_session.query(Invoice).count() == 0
    assert db_session.query(ProcessingJob).count() == 0
