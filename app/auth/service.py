from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.auth import password_service
from app.auth.models import User
from app.auth.password_policy import PasswordPolicy, PasswordPolicyError
from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.auth.repository import add_user
from app.auth.repository import get_by_phone as repository_get_by_phone

DEFAULT_PASSWORD_POLICY = PasswordPolicy(min_length=8, max_length=128)


class CreateUserError(StrEnum):
    DUPLICATE_PHONE = "duplicate_phone"
    INVALID_PASSWORD = "invalid_password"
    INVALID_PHONE = "invalid_phone"


@dataclass(frozen=True)
class CreateUserResult:
    user: User | None = None
    error: CreateUserError | None = None

    @property
    def succeeded(self) -> bool:
        return self.user is not None and self.error is None


def create_user(
    session: Session,
    phone: str,
    raw_password: str,
    is_active: bool = True,
) -> CreateUserResult:
    try:
        normalized_phone = normalize_uzbekistan_phone(phone)
    except PhoneNormalizationError:
        return CreateUserResult(error=CreateUserError.INVALID_PHONE)

    try:
        DEFAULT_PASSWORD_POLICY.validate_new_password(raw_password)
    except PasswordPolicyError:
        return CreateUserResult(error=CreateUserError.INVALID_PASSWORD)

    if repository_get_by_phone(session, normalized_phone) is not None:
        return CreateUserResult(error=CreateUserError.DUPLICATE_PHONE)

    user = User(
        phone=normalized_phone,
        password_hash=password_service.hash_password(raw_password),
        is_active=is_active,
    )
    return CreateUserResult(user=add_user(session, user))


def get_by_phone(session: Session, normalized_phone: str) -> User | None:
    return repository_get_by_phone(session, normalized_phone)


def set_user_password(user: User, raw_password: str) -> CreateUserError | None:
    try:
        DEFAULT_PASSWORD_POLICY.validate_new_password(raw_password)
    except PasswordPolicyError:
        return CreateUserError.INVALID_PASSWORD

    user.password_hash = password_service.hash_password(raw_password)
    return None


def authenticate(
    session: Session,
    phone_input: str,
    raw_password: str,
) -> User | None:
    try:
        normalized_phone = normalize_uzbekistan_phone(phone_input)
    except PhoneNormalizationError:
        password_service.verify_missing_user_password(raw_password)
        return None

    user = repository_get_by_phone(session, normalized_phone)
    if user is None:
        password_service.verify_missing_user_password(raw_password)
        return None
    if not user.is_active or user.password_hash is None:
        password_service.verify_missing_user_password(raw_password)
        return None

    valid, new_hash = password_service.verify_and_update(
        raw_password,
        user.password_hash,
    )
    if not valid:
        return None
    if new_hash is not None:
        user.password_hash = new_hash
    return user
