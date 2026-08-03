from __future__ import annotations

import hmac
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Final
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session as DatabaseSession

from app.auth.error_codes import ErrorCode, get_public_error_body
from app.auth.models import User
from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.customer.ports import CustomerLifecycleStatus
from app.customer.repository import lock_existing_own_customer_for_update
from app.customer_activation.contracts import decide_ordinary_telegram_unlink
from app.otp.contracts import OtpPurpose
from app.otp.issuance import invalidate_otp_challenges_for_link_change
from app.otp.repository import (
    OtpChallengeLockSet,
    get_outstanding_challenge_ids_by_user_for_purposes,
    lock_outstanding_challenge_set_by_user_for_purposes,
)
from app.settings import Settings
from app.telegram import repository as telegram_repository
from app.telegram.client_ip import ResolvedClientIp
from app.telegram.events import append_telegram_link_event
from app.telegram.inbound import (
    SensitiveTelegramContactPhone,
    TelegramUserIdentity,
    VerifiedPrivateTelegramChatIdentity,
)
from app.telegram.models import TelegramLink, TelegramLinkEvent, TelegramLinkToken
from app.telegram.rate_limit import record_telegram_link_issuance_attempt
from app.telegram.repository import (
    TelegramContactBindingConflict,
    TelegramLinkTokenInsertConflict,
    bind_locked_telegram_link_token_for_contact,
    delete_telegram_link_tokens_eligible_for_purge,
    get_outstanding_telegram_link_token_ids_by_user,
    get_pending_telegram_link_token_ids_by_contact_binding,
    get_telegram_link_by_user_for_update,
    get_telegram_link_token_ids_for_contact_binding,
    get_valid_telegram_link_token_for_consume_by_hash_for_update,
    has_active_telegram_link,
    invalidate_and_insert_telegram_link_token,
    invalidate_locked_outstanding_telegram_link_tokens,
    link_phone_verified_private_chat_from_prelocked_state,
    lock_outstanding_telegram_link_token_set_by_user,
    lock_telegram_link_change_set,
    lock_telegram_link_token_set_by_ids,
    relink_phone_verified_private_chat_from_prelocked_state,
    unlink_verified_private_chat_from_prelocked_state,
)
from app.telegram.token import (
    RawTelegramLinkToken,
    create_telegram_link_token,
    derive_telegram_contact_binding_mac,
    hash_telegram_link_token,
)

TELEGRAM_LINK_TOKEN_TTL_SECONDS: Final = 600
TELEGRAM_LINK_TOKEN_PURGE_DEFAULT_BATCH_SIZE: Final = 500
TELEGRAM_LINK_TOKEN_PURGE_MAX_BATCH_SIZE: Final = 5000
_EXPECTED_ACTIVE_CHAT_COLLISION_CONSTRAINTS: Final = frozenset(
    {"uq_telegram_links_active_chat_id"}
)
_EXPECTED_USER_LINK_COLLISION_CONSTRAINTS: Final = frozenset(
    {"uq_telegram_links_user_id"}
)


def get_other_active_telegram_link_by_chat_identity_for_update(
    session: DatabaseSession,
    current_user: User,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
) -> TelegramLink | None:
    """Compatibility seam retained for inherited fault-injection tests."""
    return (
        telegram_repository.get_other_active_telegram_link_by_chat_identity_for_update(
            session,
            current_user,
            chat_identity,
        )
    )


class TelegramLinkStatus(StrEnum):
    LINKED = "LINKED"
    UNLINKED = "UNLINKED"


class TelegramLinkOutcome(StrEnum):
    LINKED = "LINKED"
    RELINKED = "RELINKED"
    UNLINKED = "UNLINKED"
    ALREADY_LINKED_TO_THIS_CHAT = "ALREADY_LINKED_TO_THIS_CHAT"


TelegramStartTokenConsumeOutcome = TelegramLinkOutcome


@dataclass(frozen=True, repr=False)
class IssuedTelegramLinkToken:
    raw_token: RawTelegramLinkToken
    token: TelegramLinkToken

    def __repr__(self) -> str:
        return (
            "IssuedTelegramLinkToken(raw_token=<redacted>, token=<TelegramLinkToken>)"
        )


@dataclass(frozen=True, repr=False)
class ConsumedTelegramStartToken:
    token: TelegramLinkToken
    link: TelegramLink
    outcome: TelegramLinkOutcome
    event: TelegramLinkEvent | None = None

    def __repr__(self) -> str:
        return (
            "ConsumedTelegramStartToken("
            "token=<TelegramLinkToken>, link=<TelegramLink>, "
            f"outcome={self.outcome.value}, event=<TelegramLinkEvent | None>"
            ")"
        )


@dataclass(frozen=True, repr=False)
class PendingTelegramContactBinding:
    def __repr__(self) -> str:
        return "PendingTelegramContactBinding()"


@dataclass(frozen=True, repr=False)
class UnlinkedTelegramLink:
    link: TelegramLink
    event: TelegramLinkEvent
    invalidated_token_count: int
    outcome: TelegramLinkOutcome = TelegramLinkOutcome.UNLINKED

    def __repr__(self) -> str:
        return (
            "UnlinkedTelegramLink("
            "link=<TelegramLink>, event=<TelegramLinkEvent>, "
            f"invalidated_token_count={self.invalidated_token_count}, "
            f"outcome={self.outcome.value}"
            ")"
        )


class TelegramLinkTokenIssueError(RuntimeError):
    def __init__(
        self,
        error_code: ErrorCode,
        public_error: dict[str, str] | None = None,
    ) -> None:
        self.error_code = error_code
        self.public_error = public_error or get_public_error_body(
            error_code,
            internal_detail="telegram link token issue failed",
        )
        super().__init__(error_code.value)


class TelegramLinkTokenIssueInternalError(RuntimeError):
    pass


class TelegramLinkLifecycleInternalError(RuntimeError):
    pass


class TelegramLinkTokenConsumeError(RuntimeError):
    def __init__(self) -> None:
        self.error_code = ErrorCode.LINK_TOKEN_INVALID
        self.public_error = get_public_error_body(
            ErrorCode.LINK_TOKEN_INVALID,
            internal_detail="telegram link token consume failed",
        )
        super().__init__(ErrorCode.LINK_TOKEN_INVALID.value)


class TelegramChatAlreadyLinkedError(RuntimeError):
    def __init__(self) -> None:
        self.error_code = ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED
        self.public_error = get_public_error_body(
            ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED,
            internal_detail="telegram chat collision",
        )
        super().__init__(ErrorCode.TELEGRAM_CHAT_ALREADY_LINKED.value)


class TelegramContactVerificationError(RuntimeError):
    def __init__(self) -> None:
        self.error_code = ErrorCode.TELEGRAM_PHONE_MISMATCH
        self.public_error = get_public_error_body(
            ErrorCode.TELEGRAM_PHONE_MISMATCH,
            internal_detail="telegram contact verification failed",
        )
        super().__init__(ErrorCode.TELEGRAM_PHONE_MISMATCH.value)


def issue_link_token_after_rate_limit(
    session: DatabaseSession,
    current_user: User,
    now: datetime,
    token_generator: Callable[[int], str] | None = None,
) -> IssuedTelegramLinkToken:
    return _issue_token_for_link_state_after_rate_limit(
        session,
        current_user,
        now,
        token_generator,
        require_active_link=False,
    )


def issue_relink_token_after_rate_limit(
    session: DatabaseSession,
    current_user: User,
    now: datetime,
    token_generator: Callable[[int], str] | None = None,
) -> IssuedTelegramLinkToken:
    return _issue_token_for_link_state_after_rate_limit(
        session,
        current_user,
        now,
        token_generator,
        require_active_link=True,
    )


def record_link_token_issuance_rate_limit(
    session: DatabaseSession,
    settings: Settings,
    current_user: User,
    client_ip: ResolvedClientIp,
    now: datetime,
) -> None:
    current_time = _as_utc(now)
    try:
        canonical_user = _get_canonical_current_user(session, current_user)
        rate_limit_result = record_telegram_link_issuance_attempt(
            session,
            settings,
            canonical_user,
            client_ip,
            current_time,
        )
        if not rate_limit_result.allowed:
            raise TelegramLinkTokenIssueError(
                rate_limit_result.error_code or ErrorCode.RATE_LIMITED,
                public_error=rate_limit_result.public_error,
            )
    except TelegramLinkTokenIssueError:
        raise
    except SQLAlchemyError:
        raise TelegramLinkTokenIssueInternalError(
            "Telegram link token rate limit failed"
        ) from None


def _issue_token_for_link_state_after_rate_limit(
    session: DatabaseSession,
    current_user: User,
    now: datetime,
    token_generator: Callable[[int], str] | None,
    *,
    require_active_link: bool,
) -> IssuedTelegramLinkToken:
    current_time = _as_utc(now)
    try:
        canonical_user = _get_canonical_current_user(session, current_user)

        has_active_link_candidate = has_active_telegram_link(session, canonical_user)
        if require_active_link and not has_active_link_candidate:
            raise TelegramLinkTokenIssueError(ErrorCode.TELEGRAM_NOT_LINKED)
        if not require_active_link and has_active_link_candidate:
            raise TelegramLinkTokenIssueError(ErrorCode.TELEGRAM_ALREADY_LINKED)

        raw_token = create_telegram_link_token(token_generator)
        token_hash = hash_telegram_link_token(raw_token)
        expires_at = current_time + timedelta(seconds=TELEGRAM_LINK_TOKEN_TTL_SECONDS)
        with session.begin_nested():
            token = invalidate_and_insert_telegram_link_token(
                session,
                canonical_user,
                token_hash,
                current_time,
                expires_at,
            )
            canonical_user = _lock_active_user(
                session,
                user_id=canonical_user.id,
            )
            if canonical_user is None:
                raise TelegramLinkTokenIssueError(ErrorCode.UNAUTHORIZED)
            existing_link = get_telegram_link_by_user_for_update(
                session,
                canonical_user,
            )
            has_active_link = _is_active_link(existing_link)
            if require_active_link and not has_active_link:
                raise TelegramLinkTokenIssueError(ErrorCode.TELEGRAM_NOT_LINKED)
            if not require_active_link and has_active_link:
                raise TelegramLinkTokenIssueError(ErrorCode.TELEGRAM_ALREADY_LINKED)
    except TelegramLinkTokenInsertConflict:
        raise TelegramLinkTokenIssueError(
            ErrorCode.RATE_LIMITED,
            public_error=get_public_error_body(
                ErrorCode.RATE_LIMITED,
                internal_detail="telegram link token insert conflict",
            ),
        ) from None
    except TelegramLinkTokenIssueError:
        raise
    except SQLAlchemyError:
        raise TelegramLinkTokenIssueInternalError(
            "Telegram link token issue failed"
        ) from None

    return IssuedTelegramLinkToken(raw_token=raw_token, token=token)


def get_valid_link_token_for_consume(
    session: DatabaseSession,
    raw_token: RawTelegramLinkToken | str,
    now: datetime,
) -> TelegramLinkToken:
    current_time = _as_utc(now)
    try:
        normalized_raw_token = _coerce_raw_link_token(raw_token)
    except ValueError:
        raise TelegramLinkTokenConsumeError() from None

    token_hash = hash_telegram_link_token(normalized_raw_token)
    token = get_valid_telegram_link_token_for_consume_by_hash_for_update(
        session,
        token_hash,
        current_time,
    )
    if token is None:
        raise TelegramLinkTokenConsumeError()
    return token


def bind_start_token_for_contact(
    session: DatabaseSession,
    raw_token: RawTelegramLinkToken,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    sender_identity: TelegramUserIdentity,
    *,
    rate_limit_hmac_key: SecretStr,
    now: datetime,
) -> PendingTelegramContactBinding:
    current_time = _as_utc(now)
    try:
        normalized_raw_token = _coerce_raw_link_token(raw_token)
    except ValueError:
        raise TelegramLinkTokenConsumeError() from None
    token_hash = hash_telegram_link_token(normalized_raw_token)
    binding_mac = derive_telegram_contact_binding_mac(
        rate_limit_hmac_key=rate_limit_hmac_key,
        chat_identity=chat_identity,
        sender_identity=sender_identity,
    )
    try:
        candidate_ids = get_telegram_link_token_ids_for_contact_binding(
            session,
            token_hash=token_hash,
            binding_mac=binding_mac,
        )
        locked_tokens = lock_telegram_link_token_set_by_ids(
            session,
            token_ids=candidate_ids,
        )
        locked_ids = tuple(token.id for token in locked_tokens)
        if locked_ids != candidate_ids:
            raise TelegramLinkTokenConsumeError()
        if (
            get_telegram_link_token_ids_for_contact_binding(
                session,
                token_hash=token_hash,
                binding_mac=binding_mac,
            )
            != locked_ids
        ):
            raise TelegramLinkTokenConsumeError()
        target = next(
            (token for token in locked_tokens if token.token_hash == token_hash),
            None,
        )
        if (
            target is None
            or target.consumed_at is not None
            or target.invalidated_at is not None
            or target.created_at > current_time
            or target.expires_at <= current_time
        ):
            raise TelegramLinkTokenConsumeError()
        stored_binding_mac = binding_mac.as_stored_value()
        if target.pending_contact_binding_mac is not None:
            if target.pending_contact_binding_mac != stored_binding_mac:
                raise TelegramLinkTokenConsumeError()
            return PendingTelegramContactBinding()
        target = bind_locked_telegram_link_token_for_contact(
            session,
            locked_tokens=locked_tokens,
            target_token_hash=token_hash,
            binding_mac=binding_mac,
            now=current_time,
        )
    except TelegramContactBindingConflict:
        raise TelegramLinkTokenConsumeError() from None
    except TelegramLinkTokenConsumeError:
        raise
    except SQLAlchemyError:
        raise TelegramLinkLifecycleInternalError(
            "Telegram contact binding failed"
        ) from None
    return PendingTelegramContactBinding()


def consume_start_token(
    session: DatabaseSession,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    sender_identity: TelegramUserIdentity,
    contact_identity: TelegramUserIdentity,
    contact_phone: SensitiveTelegramContactPhone,
    *,
    rate_limit_hmac_key: SecretStr,
    now: datetime,
) -> ConsumedTelegramStartToken:
    try:
        current_time = _as_utc(now)
        if (
            not isinstance(sender_identity, TelegramUserIdentity)
            or not isinstance(contact_identity, TelegramUserIdentity)
            or sender_identity.as_bigint() != contact_identity.as_bigint()
        ):
            raise TelegramContactVerificationError()
        binding_mac = derive_telegram_contact_binding_mac(
            rate_limit_hmac_key=rate_limit_hmac_key,
            chat_identity=chat_identity,
            sender_identity=sender_identity,
        )
        candidate_ids = get_pending_telegram_link_token_ids_by_contact_binding(
            session,
            binding_mac=binding_mac,
        )
        locked_tokens = lock_telegram_link_token_set_by_ids(
            session,
            token_ids=candidate_ids,
        )
        locked_ids = tuple(token.id for token in locked_tokens)
        if len(locked_tokens) != 1 or locked_ids != candidate_ids:
            raise TelegramLinkTokenConsumeError()
        if (
            get_pending_telegram_link_token_ids_by_contact_binding(
                session,
                binding_mac=binding_mac,
            )
            != locked_ids
        ):
            raise TelegramLinkTokenConsumeError()
        token = locked_tokens[0]
        if (
            token.pending_contact_binding_mac != binding_mac.as_stored_value()
            or token.contact_requested_at is None
            or token.contact_requested_at > current_time
            or token.consumed_at is not None
            or token.invalidated_at is not None
            or token.created_at > current_time
            or token.expires_at <= current_time
        ):
            raise TelegramLinkTokenConsumeError()

        locked_otp = _lock_link_change_otp_state(session, user_id=token.user_id)
        token_user = _lock_active_user(session, user_id=token.user_id)
        if token_user is None:
            raise TelegramLinkTokenConsumeError()
        if not _link_change_otp_state_is_current(
            session,
            user_id=token_user.id,
            locked=locked_otp,
        ):
            raise TelegramLinkTokenConsumeError()
        if not _contact_phone_matches_user(contact_phone, token_user):
            raise TelegramContactVerificationError()
        locked_links = lock_telegram_link_change_set(
            session,
            token_user,
            chat_identity,
        )
        existing_link = next(
            (link for link in locked_links if link.user_id == token_user.id),
            None,
        )
        conflicting_link = next(
            (
                link
                for link in locked_links
                if link.user_id != token_user.id
                and link.telegram_chat_id == chat_identity.as_bigint()
                and link.unlinked_at is None
            ),
            None,
        )
        if conflicting_link is not None:
            raise TelegramChatAlreadyLinkedError()
        lock_existing_own_customer_for_update(
            session,
            actor_user_id=token_user.id,
        )

        if _is_active_link(existing_link):
            link = _mutate_link_with_collision_recovery(
                session,
                relink_phone_verified_private_chat_from_prelocked_state,
                current_user=token_user,
                chat_identity=chat_identity,
                now=current_time,
                existing_link=existing_link,
            )
            event_action = "relinked"
            outcome = TelegramLinkOutcome.RELINKED
        else:
            link = _mutate_link_with_collision_recovery(
                session,
                link_phone_verified_private_chat_from_prelocked_state,
                current_user=token_user,
                chat_identity=chat_identity,
                now=current_time,
                existing_link=existing_link,
            )
            event_action = "linked"
            outcome = TelegramLinkOutcome.LINKED
        if link is None:
            raise TelegramLinkTokenConsumeError()

        token.consumed_at = current_time
        token.pending_contact_binding_mac = None
        token.contact_requested_at = None
        session.add(token)
        session.flush()
        invalidate_otp_challenges_for_link_change(
            session,
            user_id=token_user.id,
            purposes=(OtpPurpose.LOGIN, OtpPurpose.REGISTRATION),
            now=current_time,
            locked=locked_otp,
        )
        event = append_telegram_link_event(
            session,
            token.user_id,
            event_action,
            current_time,
        )
    except (
        TelegramContactVerificationError,
        TelegramLinkTokenConsumeError,
        TelegramChatAlreadyLinkedError,
    ):
        raise
    except SQLAlchemyError:
        raise TelegramLinkLifecycleInternalError(
            "Telegram link lifecycle transition failed"
        ) from None
    return ConsumedTelegramStartToken(
        token=token,
        link=link,
        outcome=outcome,
        event=event,
    )


def get_link_status(
    session: DatabaseSession,
    current_user: User,
) -> TelegramLinkStatus:
    canonical_user = _get_canonical_current_user(session, current_user)
    if has_active_telegram_link(session, canonical_user):
        return TelegramLinkStatus.LINKED
    return TelegramLinkStatus.UNLINKED


def unlink(
    session: DatabaseSession,
    current_user: User,
    now: datetime,
) -> UnlinkedTelegramLink:
    try:
        current_time = _as_utc(now)
        canonical_user = _get_canonical_current_user(session, current_user)
        with session.begin_nested():
            locked_tokens = lock_outstanding_telegram_link_token_set_by_user(
                session,
                user_id=canonical_user.id,
            )
            locked_otp = _lock_link_change_otp_state(
                session,
                user_id=canonical_user.id,
            )
            canonical_user = _lock_active_user(
                session,
                user_id=canonical_user.id,
            )
            if canonical_user is None:
                raise TelegramLinkTokenIssueError(ErrorCode.UNAUTHORIZED)
            if not _link_change_otp_state_is_current(
                session,
                user_id=canonical_user.id,
                locked=locked_otp,
            ):
                raise TelegramLinkTokenIssueError(ErrorCode.RATE_LIMITED)
            locked_token_ids = tuple(token.id for token in locked_tokens)
            if (
                get_outstanding_telegram_link_token_ids_by_user(
                    session,
                    user_id=canonical_user.id,
                )
                != locked_token_ids
            ):
                raise TelegramLinkTokenIssueError(ErrorCode.RATE_LIMITED)
            existing_link = get_telegram_link_by_user_for_update(
                session,
                canonical_user,
            )
            customer = lock_existing_own_customer_for_update(
                session,
                actor_user_id=canonical_user.id,
            )
            customer_status = (
                CustomerLifecycleStatus(customer.onboarding_status)
                if customer is not None
                else None
            )
            unlink_decision = decide_ordinary_telegram_unlink(customer_status)
            if not unlink_decision.mutation_allowed:
                raise TelegramLinkTokenIssueError(
                    ErrorCode.TELEGRAM_REQUIRED_FOR_ACTIVE_CUSTOMER
                )
            if not _is_active_link(existing_link):
                raise TelegramLinkTokenIssueError(ErrorCode.TELEGRAM_NOT_LINKED)
            invalidated_token_count = (
                invalidate_locked_outstanding_telegram_link_tokens(
                    session,
                    locked_tokens=locked_tokens,
                    now=current_time,
                )
            )
            link = unlink_verified_private_chat_from_prelocked_state(
                session,
                canonical_user,
                current_time,
                existing_link=existing_link,
            )
            if link is None:
                raise TelegramLinkTokenIssueError(ErrorCode.TELEGRAM_NOT_LINKED)
            invalidate_otp_challenges_for_link_change(
                session,
                user_id=canonical_user.id,
                purposes=(OtpPurpose.LOGIN, OtpPurpose.REGISTRATION),
                now=current_time,
                locked=locked_otp,
            )
            event = append_telegram_link_event(
                session,
                canonical_user.id,
                "unlinked",
                current_time,
            )
    except TelegramLinkTokenIssueError:
        raise
    except SQLAlchemyError:
        raise TelegramLinkLifecycleInternalError(
            "Telegram link lifecycle transition failed"
        ) from None
    return UnlinkedTelegramLink(
        link=link,
        event=event,
        invalidated_token_count=invalidated_token_count,
    )


def purge_terminal_link_tokens(
    session: DatabaseSession,
    now: datetime,
    batch_size: int = TELEGRAM_LINK_TOKEN_PURGE_DEFAULT_BATCH_SIZE,
) -> int:
    current_time = _as_utc(now)
    _validate_purge_batch_size(batch_size)
    try:
        return delete_telegram_link_tokens_eligible_for_purge(
            session,
            current_time,
            limit=batch_size,
        )
    except SQLAlchemyError:
        raise TelegramLinkLifecycleInternalError(
            "Telegram link token purge failed"
        ) from None


def _get_canonical_current_user(
    session: DatabaseSession,
    current_user: User,
) -> User:
    user = session.get(User, current_user.id)
    if user is None or not user.is_active:
        raise TelegramLinkTokenIssueError(ErrorCode.UNAUTHORIZED)
    return user


def _get_token_user(
    session: DatabaseSession,
    token: TelegramLinkToken,
) -> User:
    user = session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise TelegramLinkTokenConsumeError()
    return user


def _lock_link_change_otp_state(
    session: DatabaseSession,
    *,
    user_id: UUID,
) -> OtpChallengeLockSet:
    return lock_outstanding_challenge_set_by_user_for_purposes(
        session,
        user_id=user_id,
        purposes=(OtpPurpose.LOGIN, OtpPurpose.REGISTRATION),
    )


def _lock_active_user(
    session: DatabaseSession,
    *,
    user_id: UUID,
) -> User | None:
    user = session.get(User, user_id, with_for_update=True)
    if user is None or not user.is_active:
        return None
    return user


def _link_change_otp_state_is_current(
    session: DatabaseSession,
    *,
    user_id: UUID,
    locked: OtpChallengeLockSet,
) -> bool:
    locked_ids = tuple(challenge.id for challenge in locked.challenges)
    return locked_ids == get_outstanding_challenge_ids_by_user_for_purposes(
        session,
        user_id=user_id,
        purposes=(OtpPurpose.LOGIN, OtpPurpose.REGISTRATION),
    )


def _is_active_link(link: TelegramLink | None) -> bool:
    return (
        link is not None
        and link.telegram_chat_id is not None
        and link.unlinked_at is None
    )


def _contact_phone_matches_user(
    contact_phone: SensitiveTelegramContactPhone,
    user: User,
) -> bool:
    if not isinstance(contact_phone, SensitiveTelegramContactPhone):
        raise TypeError("Telegram contact phone must be typed")
    try:
        normalized_contact = normalize_uzbekistan_phone(
            contact_phone.as_normalization_input()
        )
        normalized_user = normalize_uzbekistan_phone(user.phone)
    except PhoneNormalizationError:
        return False
    return normalized_user == user.phone and hmac.compare_digest(
        normalized_contact,
        normalized_user,
    )


def _mutate_link_with_collision_recovery(
    session: DatabaseSession,
    mutation: Callable[
        [
            DatabaseSession,
            User,
            VerifiedPrivateTelegramChatIdentity,
            datetime,
        ],
        TelegramLink | None,
    ],
    *,
    current_user: User,
    chat_identity: VerifiedPrivateTelegramChatIdentity,
    now: datetime,
    existing_link: TelegramLink | None,
) -> TelegramLink | None:
    try:
        with session.begin_nested():
            return mutation(
                session,
                current_user,
                chat_identity,
                now,
                existing_link=existing_link,
            )
    except IntegrityError as exc:
        if _is_expected_chat_collision_integrity_error(exc):
            raise TelegramChatAlreadyLinkedError() from None
        if _is_expected_user_link_collision_integrity_error(exc):
            raise TelegramLinkTokenConsumeError() from None
        raise


def _is_expected_chat_collision_integrity_error(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name in _EXPECTED_ACTIVE_CHAT_COLLISION_CONSTRAINTS


def _is_expected_user_link_collision_integrity_error(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    return constraint_name in _EXPECTED_USER_LINK_COLLISION_CONSTRAINTS


def _validate_purge_batch_size(batch_size: int) -> None:
    if (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or batch_size < 1
        or batch_size > TELEGRAM_LINK_TOKEN_PURGE_MAX_BATCH_SIZE
    ):
        raise ValueError("Telegram link token purge batch size must be 1..5000")


def _coerce_raw_link_token(
    raw_token: RawTelegramLinkToken | str,
) -> RawTelegramLinkToken:
    if isinstance(raw_token, RawTelegramLinkToken):
        return raw_token
    return RawTelegramLinkToken(raw_token)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Telegram link token issue timestamp must be timezone-aware")
    return value.astimezone(UTC)
