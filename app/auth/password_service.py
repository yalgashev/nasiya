from pwdlib import PasswordHash

DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$AxWg6Ev5sFrRjewJR08Hjw"
    "$05UVhyaF7vF2auHP+jR8Xn0EDnU6f5k4639q0zYXAVM"
)
_PASSWORD_HASH = PasswordHash.recommended()


class PasswordHashingError(RuntimeError):
    pass


def hash_password(raw_password: str) -> str:
    try:
        return _PASSWORD_HASH.hash(raw_password)
    except Exception:
        raise PasswordHashingError("Password hashing failed") from None


def verify_password(raw_password: str, stored_hash: str) -> bool:
    try:
        return _PASSWORD_HASH.verify(raw_password, stored_hash)
    except Exception:
        return False


def verify_and_update(
    raw_password: str,
    stored_hash: str,
) -> tuple[bool, str | None]:
    try:
        return _PASSWORD_HASH.verify_and_update(raw_password, stored_hash)
    except Exception:
        return False, None


def verify_missing_user_password(raw_password: str) -> None:
    verify_password(raw_password, DUMMY_PASSWORD_HASH)
