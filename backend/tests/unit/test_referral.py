import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.referral import ReferralDirectoryEntry


def test_referral_defaults():
    engine = create_engine("sqlite://")
    ReferralDirectoryEntry.__table__.create(engine)
    with Session(engine) as session:
        row = ReferralDirectoryEntry(
            topic="Registration", office="Registrar", contact_url="https://www.pnw.edu/registrar/"
        )
        session.add(row)
        session.flush()
        assert row.active is True
        assert row.campus == "all"
    engine.dispose()


@pytest.mark.parametrize(
    "values",
    [
        dict(),
        {"phone": " "},
        {"contact_url": "http://example.edu"},
        {"email": "invalid"},
        {"phone": "123", "campus": "other"},
    ],
)
def test_invalid_referral(values):
    engine = create_engine("sqlite://")
    ReferralDirectoryEntry.__table__.create(engine)
    with Session(engine) as session:
        with pytest.raises(ValueError):
            session.add(ReferralDirectoryEntry(topic="Test", office="Office", **values))
            session.flush()
    engine.dispose()
