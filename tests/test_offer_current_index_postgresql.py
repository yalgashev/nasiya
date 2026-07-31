import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from app.offers.enums import OfferPurpose, OfferStatus

pytestmark = pytest.mark.integration


def test_postgresql_partial_unique_index_rejects_second_current_per_purpose(
    test_database_engine: Engine,
) -> None:
    with test_database_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(
                text(
                    "CREATE TEMPORARY TABLE m9_offer_current_index_probe ("
                    "purpose varchar(32) NOT NULL, "
                    "status varchar(16) NOT NULL"
                    ") ON COMMIT DROP"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX uq_offer_versions_current_purpose "
                    "ON m9_offer_current_index_probe (purpose) "
                    "WHERE status = 'CURRENT'"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO m9_offer_current_index_probe "
                    "(purpose, status) VALUES (:purpose, :status)"
                ),
                {
                    "purpose": OfferPurpose.REGISTRATION.value,
                    "status": OfferStatus.CURRENT.value,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO m9_offer_current_index_probe "
                    "(purpose, status) VALUES (:purpose, :status)"
                ),
                {
                    "purpose": OfferPurpose.REGISTRATION.value,
                    "status": OfferStatus.APPROVED.value,
                },
            )

            with pytest.raises(IntegrityError) as exc_info:
                with connection.begin_nested():
                    connection.execute(
                        text(
                            "INSERT INTO m9_offer_current_index_probe "
                            "(purpose, status) VALUES (:purpose, :status)"
                        ),
                        {
                            "purpose": OfferPurpose.REGISTRATION.value,
                            "status": OfferStatus.CURRENT.value,
                        },
                    )

            assert (
                exc_info.value.orig.diag.constraint_name
                == "uq_offer_versions_current_purpose"
            )
            connection.execute(
                text(
                    "INSERT INTO m9_offer_current_index_probe "
                    "(purpose, status) VALUES (:purpose, :status)"
                ),
                {
                    "purpose": OfferPurpose.DEBT_ACCEPTANCE.value,
                    "status": OfferStatus.CURRENT.value,
                },
            )
        finally:
            transaction.rollback()
