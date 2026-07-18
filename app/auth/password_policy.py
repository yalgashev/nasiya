from dataclasses import dataclass

from app.settings import Settings


class PasswordPolicyError(ValueError):
    pass


@dataclass(frozen=True, repr=False)
class PasswordPolicy:
    min_length: int
    max_length: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "PasswordPolicy":
        return cls(
            min_length=settings.password_min_length,
            max_length=settings.password_max_length,
        )

    def validate_new_password(self, raw_password: str) -> None:
        if not raw_password.strip():
            raise PasswordPolicyError("Password does not meet policy")
        if not self.min_length <= len(raw_password) <= self.max_length:
            raise PasswordPolicyError("Password does not meet policy")
        if not any(character.isalpha() for character in raw_password):
            raise PasswordPolicyError("Password does not meet policy")
        if not any(character.isdecimal() for character in raw_password):
            raise PasswordPolicyError("Password does not meet policy")


def validate_new_password(raw_password: str, settings: Settings) -> None:
    PasswordPolicy.from_settings(settings).validate_new_password(raw_password)
