import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Organization, User


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


def test_organization_has_many_users(db_session):
    org = make_org(db_session)
    make_user(db_session, org, email="a@example.com")
    make_user(db_session, org, email="b@example.com")
    db_session.commit()

    db_session.refresh(org)
    assert len(org.users) == 2
    emails = {u.email for u in org.users}
    assert emails == {"a@example.com", "b@example.com"}


def test_user_belongs_to_exactly_one_organization(db_session):
    org = make_org(db_session)
    user = make_user(db_session, org)
    db_session.commit()

    db_session.refresh(user)
    assert user.organization_id == org.id
    assert user.organization.id == org.id


def test_user_requires_organization_id(db_session):
    user = User(organization_id=None, email="orphan@example.com", hashed_password="x")
    db_session.add(user)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_user_email_must_be_unique(db_session):
    org = make_org(db_session)
    make_user(db_session, org, email="dup@example.com")
    db_session.flush()

    dup = User(organization_id=org.id, email="dup@example.com", hashed_password="x")
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()


def test_deleting_organization_cascades_to_users(db_session):
    org = make_org(db_session)
    make_user(db_session, org)
    db_session.commit()

    db_session.delete(org)
    db_session.commit()

    assert db_session.query(User).count() == 0
