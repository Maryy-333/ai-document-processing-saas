from app.models import Document, Organization, ProcessingJob, User
from app.models.enums import ProcessingStage, ProcessingStatus


def make_org_user_doc(session):
    org = Organization(name="Acme")
    session.add(org)
    session.flush()
    user = User(organization_id=org.id, email="u@example.com", hashed_password="x")
    session.add(user)
    session.flush()
    doc = Document(
        organization_id=org.id,
        uploaded_by_user_id=user.id,
        original_filename="invoice.pdf",
        storage_key=f"orgs/{org.id}/invoice.pdf",
        content_type="application/pdf",
        file_size_bytes=1,
    )
    session.add(doc)
    session.flush()
    return doc


def test_processing_job_defaults_to_pending(db_session):
    doc = make_org_user_doc(db_session)
    job = ProcessingJob(document_id=doc.id, stage=ProcessingStage.TEXT_EXTRACTION)
    db_session.add(job)
    db_session.commit()

    db_session.refresh(job)
    assert job.status == ProcessingStatus.PENDING


def test_document_has_ordered_processing_job_history(db_session):
    """
    A Document can accumulate multiple ProcessingJob rows over the pipeline —
    this is the "processing history", explicitly distinct from a full
    user-action AuditLog (which is not modeled here per the approved scope).
    """
    doc = make_org_user_doc(db_session)
    db_session.add_all(
        [
            ProcessingJob(document_id=doc.id, stage=ProcessingStage.VALIDATION),
            ProcessingJob(document_id=doc.id, stage=ProcessingStage.TEXT_EXTRACTION),
            ProcessingJob(document_id=doc.id, stage=ProcessingStage.AI_EXTRACTION),
        ]
    )
    db_session.commit()

    db_session.refresh(doc)
    assert len(doc.processing_jobs) == 3
    stages = [j.stage for j in doc.processing_jobs]
    assert ProcessingStage.VALIDATION in stages
    assert ProcessingStage.AI_EXTRACTION in stages


def test_processing_job_model_has_no_user_attribution_field():
    """
    Guards the approved correction: ProcessingJob tracks system processing
    (stage/status/duration/error/cost), not who performed a user action.
    If this test needs updating because a user-attribution column was added,
    that's a scope change that should go back to the Architect for approval
    rather than happening silently.
    """
    column_names = {c.name for c in ProcessingJob.__table__.columns}
    assert "performed_by_user_id" not in column_names
    assert "user_id" not in column_names
    assert {"stage", "status", "duration_ms", "error_category", "cost_usd"} <= column_names
