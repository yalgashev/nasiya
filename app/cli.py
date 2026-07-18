import argparse
import getpass
import sys
from collections.abc import Sequence
from typing import TextIO

from sqlalchemy.exc import SQLAlchemyError

from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.auth.service import (
    CreateUserError,
    create_user,
    get_by_phone,
    set_user_password,
)
from app.db import create_database_engine, create_database_session_factory
from app.settings import Settings

LOCAL_ENVIRONMENTS = frozenset({"development", "local", "testing"})


class CliError(RuntimeError):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_local_user = subparsers.add_parser("create-local-user")
    create_local_user.add_argument("--phone", required=True)
    create_local_user.add_argument(
        "--reset-password",
        action="store_true",
        help="Reset password for an existing local user.",
    )
    return parser


def load_settings() -> Settings:
    return Settings(_env_file=".env")


def ensure_local_environment(settings: Settings) -> None:
    environment = settings.app_environment.strip().casefold()
    if environment not in LOCAL_ENVIRONMENTS:
        raise CliError("This command is only available in local development")


def prompt_password_twice() -> str:
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise CliError("Passwords do not match")
    return password


def create_or_update_local_user(args: argparse.Namespace, settings: Settings) -> int:
    ensure_local_environment(settings)
    engine = create_database_engine(settings)
    session_factory = create_database_session_factory(engine)
    try:
        with session_factory() as session:
            try:
                normalized_phone = normalize_uzbekistan_phone(args.phone)
            except PhoneNormalizationError:
                print("Invalid phone number", file=sys.stderr)
                return 2

            existing_user = get_by_phone(session, normalized_phone)
            if existing_user is not None and not args.reset_password:
                print("Local user already exists; password unchanged.")
                return 0

            password = prompt_password_twice()
            if existing_user is not None:
                error = set_user_password(existing_user, password)
                if error is not None:
                    print("Password does not meet policy", file=sys.stderr)
                    session.rollback()
                    return 2
                session.commit()
                print("Local user password updated.")
                return 0

            result = create_user(session, args.phone, password)
            if result.error == CreateUserError.DUPLICATE_PHONE:
                session.rollback()
                print("Local user already exists; password unchanged.")
                return 0
            if result.error == CreateUserError.INVALID_PHONE:
                session.rollback()
                print("Invalid phone number", file=sys.stderr)
                return 2
            if result.error == CreateUserError.INVALID_PASSWORD:
                session.rollback()
                print("Password does not meet policy", file=sys.stderr)
                return 2
            session.commit()
            print("Local user created.")
            return 0
    except SQLAlchemyError:
        print("Database operation failed", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def main(
    argv: Sequence[str] | None = None,
    settings: Settings | None = None,
    stderr: TextIO | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    effective_settings = settings or load_settings()
    try:
        if args.command == "create-local-user":
            return create_or_update_local_user(args, effective_settings)
    except CliError as exc:
        print(str(exc), file=stderr or sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
