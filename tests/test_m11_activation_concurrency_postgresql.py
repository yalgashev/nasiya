import logging
from uuid import uuid4

import pytest
from sqlalchemy.engine import Engine

import tests.test_m11_registration_verify_postgresql as verify_tests
from app.auth.sessions import RawSessionToken
from app.auth.user_agent import MAX_USER_AGENT_LENGTH
from app.customer_activation.contracts import (
    ACTIVATION_ATOMIC_MUTATION_ORDER,
    ActivationAtomicMutation,
    ActivationCsrfSecret,
    ActivationSafeDeviceMetadata,
    ActivationSessionRotation,
    ActivationSessionRotationScope,
    ActivationSessionSecrets,
    CommittedCustomerActivation,
    CustomerActivationAlreadyActive,
    PreparedCustomerActivation,
    mark_customer_activation_committed,
)

OLD_TOKEN = "old-session-token-secret"
NEW_TOKEN = "new-session-token-secret"
OLD_CSRF = "old-csrf-secret"
NEW_CSRF = "new-csrf-secret"


def build_rotation() -> ActivationSessionRotation:
    return ActivationSessionRotation(
        previous_session_id=uuid4(),
        replacement_session_id=uuid4(),
        user_id=uuid4(),
        active_shop_id=uuid4(),
        safe_device_metadata=ActivationSafeDeviceMetadata(
            user_agent="synthetic-browser"
        ),
        _replacement_secrets=ActivationSessionSecrets(
            token=RawSessionToken(NEW_TOKEN),
            csrf_secret=ActivationCsrfSecret(NEW_CSRF),
        ),
    )


def test_atomic_activation_mutation_order_is_exact_and_immutable() -> None:
    prepared = PreparedCustomerActivation(_rotation=build_rotation())

    assert prepared.mutations == (
        ActivationAtomicMutation.CHALLENGE_CONSUMED,
        ActivationAtomicMutation.OTP_CONSUMED_EVENT_APPENDED,
        ActivationAtomicMutation.CUSTOMER_ACTIVATED,
        ActivationAtomicMutation.CENTRAL_AUDIT_APPENDED,
        ActivationAtomicMutation.CURRENT_SESSION_REPLACED,
    )
    assert prepared.mutations is ACTIVATION_ATOMIC_MUTATION_ORDER
    with pytest.raises(AttributeError):
        prepared.mutations = ()  # type: ignore[misc]


def test_rotation_is_current_only_and_preserves_safe_context() -> None:
    rotation = build_rotation()

    assert rotation.scope is ActivationSessionRotationScope.CURRENT_SESSION_ONLY
    assert rotation.active_shop_id is not None
    assert rotation.safe_device_metadata.user_agent == "synthetic-browser"
    assert not hasattr(rotation, "other_session_ids")
    assert not hasattr(rotation, "revoke_other_sessions")


def test_safe_device_metadata_is_bounded() -> None:
    metadata = ActivationSafeDeviceMetadata(user_agent="x" * 600)

    assert metadata.user_agent == "x" * MAX_USER_AGENT_LENGTH


def test_session_rotation_secrets_are_redacted_at_every_contract_layer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    rotation = build_rotation()
    prepared = PreparedCustomerActivation(_rotation=rotation)
    committed = mark_customer_activation_committed(prepared)
    with caplog.at_level(logging.INFO):
        logging.getLogger("tests.m11.activation-secrets").info(
            "activation contracts %r %r %r",
            rotation,
            prepared,
            committed,
        )

    rendered = " ".join(
        (
            repr(rotation._replacement_secrets),
            repr(rotation),
            repr(prepared),
            repr(committed),
            str(rotation._replacement_secrets.csrf_secret),
            caplog.text,
        )
    )
    for secret in (OLD_TOKEN, NEW_TOKEN, OLD_CSRF, NEW_CSRF):
        assert secret not in rendered


def test_cookie_token_is_released_only_from_committed_result() -> None:
    prepared = PreparedCustomerActivation(_rotation=build_rotation())

    assert not hasattr(prepared, "release_cookie_token")
    committed = mark_customer_activation_committed(prepared)

    assert isinstance(committed, CommittedCustomerActivation)
    assert committed.release_cookie_token().as_cookie_value() == NEW_TOKEN


def test_already_active_result_has_no_rotation_or_cookie_release() -> None:
    result = CustomerActivationAlreadyActive()

    assert not hasattr(result, "rotation")
    assert not hasattr(result, "release_cookie_token")


def test_rotation_rejects_same_session_identity() -> None:
    session_id = uuid4()

    with pytest.raises(ValueError, match="replacement session must be fresh"):
        ActivationSessionRotation(
            previous_session_id=session_id,
            replacement_session_id=session_id,
            user_id=uuid4(),
            active_shop_id=None,
            safe_device_metadata=ActivationSafeDeviceMetadata(user_agent=None),
            _replacement_secrets=ActivationSessionSecrets(
                token=RawSessionToken(NEW_TOKEN),
                csrf_secret=ActivationCsrfSecret(NEW_CSRF),
            ),
        )


def test_parallel_correct_verify_has_one_activation_winner(
    m2_test_database: Engine,
) -> None:
    verify_tests.test_parallel_correct_verify_has_one_activation_winner(
        m2_test_database
    )


def test_replay_and_already_active_are_zero_write_noop_success(
    m2_test_database: Engine,
) -> None:
    verify_tests.test_replay_and_already_active_are_zero_write_noop_success(
        m2_test_database
    )
