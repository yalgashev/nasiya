from __future__ import annotations

import pytest
from sqlalchemy.engine import Engine

import tests.test_m11_registration_verify_postgresql as verify_tests

pytestmark = pytest.mark.integration


def test_identity_update_and_activation_serialize_on_customer_then_identity(
    m2_test_database: Engine,
) -> None:
    verify_tests.test_identity_update_holding_customer_lock_invalidates_waiting_activation(
        m2_test_database
    )


def test_document_supersede_object_state_and_activation_serialize(
    m2_test_database: Engine,
) -> None:
    verify_tests.test_document_object_transition_holding_lock_invalidates_waiting_activation(
        m2_test_database
    )
