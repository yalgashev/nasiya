import argparse
import asyncio
import getpass
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TextIO
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError

from app.auth.error_codes import ErrorCode
from app.auth.phone import PhoneNormalizationError, normalize_uzbekistan_phone
from app.auth.service import (
    CreateUserError,
    create_user,
    get_by_phone,
    set_user_password,
)
from app.db import create_database_engine, create_database_session_factory
from app.offers.authorization import (
    PlatformAdminBootstrapStatus,
    bootstrap_first_platform_admin,
)
from app.settings import ObjectStorageSettingsError, Settings
from app.shop import repository as shop_repository
from app.shop.enums import ShopRole, ShopStatus
from app.shop.service import (
    AddStaffResult,
    ChangeStaffRoleResult,
    ProvisionActiveShopError,
    ShopStatusTransitionOutcome,
    ShopStatusTransitionResult,
    add_staff,
    change_staff_role,
    provision_active_shop,
    reactivate_shop,
    suspend_shop,
)
from app.shop.values import ShopId, ShopStaffId, UserId
from app.storage.contracts import (
    BucketName,
    ObjectStorageService,
    StorageProviderError,
    StorageProviderOperationResult,
)
from app.storage.errors import StorageUploadError
from app.storage.s3 import S3ObjectStorageService, create_s3_client
from app.storage.service import (
    StorageDeleteBatchResult,
    StorageReconcileResult,
    delete_available_object,
    reconcile_stale_object_deletes,
    reconcile_stale_object_uploads,
)
from app.storage.smoke import fetch_presigned_smoke_object, run_storage_smoke
from app.telegram.client_ip import ResolvedClientIp

LOCAL_ENVIRONMENTS = frozenset({"development", "local", "testing"})
DEMO_USER_PASSWORD = "DemoPassword123"
DEMO_SHOP_A_ID = UUID("11111111-1111-4111-8111-111111111111")
DEMO_SHOP_B_ID = UUID("22222222-2222-4222-8222-222222222222")
DEMO_OWNER_A_PHONE = "+998900005001"
DEMO_MANAGER_A_PHONE = "+998900005002"
DEMO_CASHIER_A_PHONE = "+998900005003"
DEMO_OWNER_B_PHONE = "+998900005004"
DEMO_SHOP_A_NAME = "Demo Shop A"
DEMO_SHOP_B_NAME = "Demo Shop B"
DEMO_SHOP_A_PHONE = "+998900005101"
DEMO_SHOP_B_PHONE = "+998900005102"
DEMO_SHOP_A_ADDRESS = "Demo address A"
DEMO_SHOP_B_ADDRESS = "Demo address B"


@dataclass(frozen=True)
class DemoShopSpec:
    label: str
    shop_id: UUID
    name: str
    phone: str
    address_text: str
    owner_phone: str


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
    bootstrap_admin = subparsers.add_parser("bootstrap-platform-admin")
    bootstrap_admin.add_argument(
        "--user-id",
        type=_nonzero_uuid,
        required=True,
    )
    shop = subparsers.add_parser("shop")
    shop_subparsers = shop.add_subparsers(dest="shop_command", required=True)

    shop_create = shop_subparsers.add_parser("create")
    shop_create.add_argument("--name", required=True)
    shop_create.add_argument("--phone", required=True)
    shop_create.add_argument("--address")
    shop_create.add_argument("--owner-phone", required=True)

    shop_suspend = shop_subparsers.add_parser("suspend")
    shop_suspend.add_argument("shop_uuid", type=UUID)
    shop_suspend.add_argument("--reason", required=True)

    shop_reactivate = shop_subparsers.add_parser("reactivate")
    shop_reactivate.add_argument("shop_uuid", type=UUID)
    shop_reactivate.add_argument("--reason", required=True)

    demo = subparsers.add_parser("demo")
    demo_subparsers = demo.add_subparsers(dest="demo_command", required=True)
    demo_subparsers.add_parser("seed")

    storage = subparsers.add_parser("storage")
    storage_subparsers = storage.add_subparsers(
        dest="storage_command",
        required=True,
    )
    storage_subparsers.add_parser("preflight")
    storage_subparsers.add_parser("health")
    storage_reconcile = storage_subparsers.add_parser("reconcile")
    storage_reconcile.add_argument(
        "--batch-size",
        type=_storage_batch_size,
        default=100,
    )
    storage_delete = storage_subparsers.add_parser("delete")
    storage_delete.add_argument(
        "--object-id",
        type=_nonzero_uuid,
        required=True,
    )
    storage_smoke = storage_subparsers.add_parser("smoke")
    storage_smoke.add_argument(
        "--actor-id",
        type=_nonzero_uuid,
        required=True,
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


def bootstrap_platform_admin(
    args: argparse.Namespace,
    settings: Settings,
) -> int:
    engine = create_database_engine(settings)
    session_factory = create_database_session_factory(engine)
    try:
        with session_factory.begin() as session:
            result = bootstrap_first_platform_admin(
                session,
                target_user_id=args.user_id,
                occurred_at=datetime.now(UTC),
            )
    except SQLAlchemyError:
        print("PLATFORM_ADMIN_BOOTSTRAP_FAILED", file=sys.stderr)
        return 1
    finally:
        engine.dispose()

    if result is PlatformAdminBootstrapStatus.BOOTSTRAPPED:
        print("PLATFORM_ADMIN_BOOTSTRAPPED")
        return 0
    if result is PlatformAdminBootstrapStatus.ADMIN_ALREADY_EXISTS:
        print("PLATFORM_ADMIN_ALREADY_EXISTS", file=sys.stderr)
        return 2
    if result is PlatformAdminBootstrapStatus.USER_NOT_FOUND:
        print("PLATFORM_ADMIN_USER_NOT_FOUND", file=sys.stderr)
        return 2
    print("PLATFORM_ADMIN_USER_INACTIVE", file=sys.stderr)
    return 2


def create_shop(args: argparse.Namespace, settings: Settings) -> int:
    ensure_local_environment(settings)
    engine = create_database_engine(settings)
    session_factory = create_database_session_factory(engine)
    try:
        with session_factory() as session:
            try:
                owner_phone = normalize_uzbekistan_phone(args.owner_phone)
            except PhoneNormalizationError:
                print("Invalid owner phone number", file=sys.stderr)
                return 2

            owner = get_by_phone(session, owner_phone)
            if owner is None:
                print("Owner user not found", file=sys.stderr)
                return 2

            shop_id = ShopId(uuid4())
            result = provision_active_shop(
                session,
                shop_id=shop_id,
                name=args.name,
                phone=args.phone,
                address_text=args.address,
                owner_user_id=UserId(owner.id),
                actor_user_id=UserId(owner.id),
            )
            if result.error is not None:
                session.rollback()
                return _print_shop_create_error(result.error)

            session.commit()
            print(f"Shop created: {result.shop.shop_id}")
            return 0
    except SQLAlchemyError:
        print("Database operation failed", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def suspend_shop_command(args: argparse.Namespace, settings: Settings) -> int:
    return _run_shop_status_transition(
        args,
        settings,
        transition_func=suspend_shop,
        transitioned_label="suspended",
        noop_label="already suspended",
    )


def reactivate_shop_command(args: argparse.Namespace, settings: Settings) -> int:
    return _run_shop_status_transition(
        args,
        settings,
        transition_func=reactivate_shop,
        transitioned_label="reactivated",
        noop_label="already active",
    )


def seed_demo(args: argparse.Namespace, settings: Settings) -> int:
    _ = args
    ensure_local_environment(settings)
    engine = create_database_engine(settings)
    session_factory = create_database_session_factory(engine)
    try:
        with session_factory() as session:
            now = datetime.now(UTC)
            demo_users = _ensure_demo_users(session)
            shop_a_spec = _demo_shop_a_spec()
            shop_b_spec = _demo_shop_b_spec()
            _ensure_demo_shop(
                session,
                spec=shop_a_spec,
                owner=demo_users[shop_a_spec.owner_phone],
                now=now,
            )
            _ensure_demo_shop(
                session,
                spec=shop_b_spec,
                owner=demo_users[shop_b_spec.owner_phone],
                now=now,
            )
            _ensure_demo_staff(
                session,
                shop_id=DEMO_SHOP_A_ID,
                actor=demo_users[DEMO_OWNER_A_PHONE],
                subject=demo_users[DEMO_MANAGER_A_PHONE],
                role=ShopRole.MANAGER,
                now=now,
            )
            _ensure_demo_staff(
                session,
                shop_id=DEMO_SHOP_A_ID,
                actor=demo_users[DEMO_OWNER_A_PHONE],
                subject=demo_users[DEMO_CASHIER_A_PHONE],
                role=ShopRole.CASHIER,
                now=now,
            )
            _ensure_demo_staff(
                session,
                shop_id=DEMO_SHOP_B_ID,
                actor=demo_users[DEMO_OWNER_B_PHONE],
                subject=demo_users[DEMO_OWNER_A_PHONE],
                role=ShopRole.MANAGER,
                now=now,
            )
            session.commit()
            print("Demo seed complete.")
            print(f"Shop A: {DEMO_SHOP_A_ID}")
            print(f"Shop B: {DEMO_SHOP_B_ID}")
            return 0
    except SQLAlchemyError:
        print("Database operation failed", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def storage_preflight_command(
    args: argparse.Namespace,
    settings: Settings,
) -> int:
    _ = args
    configured = _configure_storage_service(settings)
    if configured is None:
        return 1
    storage, bucket, close_storage = configured
    provider_failed = False
    result: StorageProviderOperationResult | None = None
    try:
        result = storage.check_bucket_access(bucket=bucket)
    except StorageProviderError:
        provider_failed = True
    finally:
        _close_storage_connection(close_storage)
    if provider_failed or result is not StorageProviderOperationResult.SUCCESS:
        print("STORAGE_PROVIDER_UNAVAILABLE", file=sys.stderr)
        return 1
    print("STORAGE_PREFLIGHT_OK")
    return 0


def storage_reconcile_command(
    args: argparse.Namespace,
    settings: Settings,
) -> int:
    configured = _configure_storage_service(settings)
    if configured is None:
        return 1
    storage, _bucket, close_storage = configured
    engine = None
    upload_result: StorageReconcileResult | None = None
    delete_result: StorageDeleteBatchResult | None = None
    workflow_failed = False
    try:
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        current_time = datetime.now(UTC)
        upload_result = reconcile_stale_object_uploads(
            session_factory,
            storage=storage,
            now=current_time,
            stale_seconds=settings.object_storage_reconcile_stale_seconds,
            batch_size=args.batch_size,
        )
        delete_result = reconcile_stale_object_deletes(
            session_factory,
            storage=storage,
            now=current_time,
            stale_seconds=settings.object_storage_reconcile_stale_seconds,
            batch_size=args.batch_size,
        )
    except (SQLAlchemyError, StorageProviderError, StorageUploadError):
        workflow_failed = True
    finally:
        if engine is not None:
            engine.dispose()
        _close_storage_connection(close_storage)
    if workflow_failed or upload_result is None or delete_result is None:
        print("STORAGE_RECONCILE_FAILED", file=sys.stderr)
        return 1
    print(_format_storage_reconcile_result(upload_result, delete_result))
    return 0


def storage_delete_command(
    args: argparse.Namespace,
    settings: Settings,
) -> int:
    ensure_local_environment(settings)
    configured = _configure_storage_service(settings)
    if configured is None:
        return 1
    storage, _bucket, close_storage = configured
    engine = None
    result = None
    workflow_failed = False
    try:
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        result = delete_available_object(
            session_factory,
            object_file_id=args.object_id,
            storage=storage,
            now=datetime.now(UTC),
        )
    except (SQLAlchemyError, StorageProviderError, StorageUploadError):
        workflow_failed = True
    finally:
        if engine is not None:
            engine.dispose()
        _close_storage_connection(close_storage)
    if workflow_failed or result is None:
        print("STORAGE_DELETE_FAILED", file=sys.stderr)
        return 1
    safe_code = result.safe_code.value if result.safe_code is not None else "NONE"
    print(f"STORAGE_DELETE status={result.status.value} code={safe_code}")
    return 0


def storage_smoke_command(
    args: argparse.Namespace,
    settings: Settings,
) -> int:
    ensure_local_environment(settings)
    configured = _configure_storage_service(settings)
    if configured is None:
        return 1
    storage, _bucket, close_storage = configured
    engine = None
    result = None
    smoke_failed = False
    try:
        engine = create_database_engine(settings)
        session_factory = create_database_session_factory(engine)
        result = asyncio.run(
            run_storage_smoke(
                session_factory,
                actor_user_id=args.actor_id,
                client_ip=ResolvedClientIp("192.0.2.1"),
                now=datetime.now(UTC),
                settings=settings,
                storage=storage,
                fetch_presigned=fetch_presigned_smoke_object,
            )
        )
    except (SQLAlchemyError, StorageProviderError, StorageUploadError):
        smoke_failed = True
    finally:
        if engine is not None:
            engine.dispose()
        _close_storage_connection(close_storage)
    if smoke_failed or result is None or result.passed_checks != result.expected_checks:
        print("STORAGE_SMOKE_FAILED", file=sys.stderr)
        return 1
    print(f"STORAGE_SMOKE_PASS checks={result.passed_checks}")
    return 0


def _run_shop_status_transition(
    args: argparse.Namespace,
    settings: Settings,
    *,
    transition_func: Callable[..., ShopStatusTransitionResult],
    transitioned_label: str,
    noop_label: str,
) -> int:
    normalized_reason = _normalize_cli_reason(args.reason)
    if normalized_reason is None:
        print("Reason is required", file=sys.stderr)
        return 2

    ensure_local_environment(settings)
    engine = create_database_engine(settings)
    session_factory = create_database_session_factory(engine)
    try:
        with session_factory() as session:
            result = transition_func(
                session,
                shop_id=ShopId(args.shop_uuid),
                actor_user_id=None,
                reason=normalized_reason,
            )
            if result.error is not None:
                session.rollback()
                return _print_shop_transition_error(result.error)

            session.commit()
            label = transitioned_label
            if result.transition.outcome is ShopStatusTransitionOutcome.NOOP:
                label = noop_label
            print(f"Shop {label}: {result.transition.shop_id}")
            return 0
    except SQLAlchemyError:
        print("Database operation failed", file=sys.stderr)
        return 1
    finally:
        engine.dispose()


def _ensure_demo_users(session) -> dict[str, object]:
    return {
        phone: _ensure_demo_user(session, phone=phone)
        for phone in (
            DEMO_OWNER_A_PHONE,
            DEMO_MANAGER_A_PHONE,
            DEMO_CASHIER_A_PHONE,
            DEMO_OWNER_B_PHONE,
        )
    }


def _ensure_demo_user(session, *, phone: str):
    existing_user = get_by_phone(session, phone)
    if existing_user is not None:
        return existing_user

    result = create_user(session, phone, DEMO_USER_PASSWORD)
    if result.error is not None:
        raise CliError("Demo user provisioning failed")
    session.flush()
    return result.user


def _ensure_demo_shop(
    session,
    *,
    spec: DemoShopSpec,
    owner,
    now: datetime,
) -> None:
    existing_shop = shop_repository.get_shop(session, shop_id=ShopId(spec.shop_id))
    if existing_shop is None:
        result = provision_active_shop(
            session,
            shop_id=ShopId(spec.shop_id),
            name=spec.name,
            phone=spec.phone,
            address_text=spec.address_text,
            owner_user_id=UserId(owner.id),
            actor_user_id=None,
            now=now,
        )
        if result.error is not None:
            raise CliError(f"{spec.label} provisioning failed")
        session.flush()
        return

    if existing_shop.name != spec.name or existing_shop.phone != spec.phone:
        raise CliError(
            f"{spec.label} fixed UUID points to an unexpected shop; "
            "aborting without mutation"
        )
    if ShopStatus(existing_shop.status) is not ShopStatus.ACTIVE:
        raise CliError(
            f"{spec.label} fixed UUID is not in expected demo state; "
            "aborting without mutation"
        )

    owner_staff = shop_repository.get_active_staff(
        session,
        shop_id=ShopId(spec.shop_id),
        user_id=UserId(owner.id),
    )
    if owner_staff is None or owner_staff.role != ShopRole.OWNER.value:
        raise CliError(
            f"{spec.label} fixed UUID is missing expected owner; "
            "aborting without mutation"
        )


def _ensure_demo_staff(
    session,
    *,
    shop_id: UUID,
    actor,
    subject,
    role: ShopRole,
    now: datetime,
) -> None:
    add_result = add_staff(
        session,
        shop_id=ShopId(shop_id),
        actor_user_id=UserId(actor.id),
        phone=subject.phone,
        role=role,
        now=now,
    )
    _raise_for_staff_service_error(add_result, "Demo staff provisioning failed")
    session.flush()

    staff = shop_repository.get_active_staff(
        session,
        shop_id=ShopId(shop_id),
        user_id=UserId(subject.id),
    )
    if staff is None:
        raise CliError("Demo staff provisioning failed")
    if staff.role == role.value:
        return

    change_result = change_staff_role(
        session,
        shop_id=ShopId(shop_id),
        actor_user_id=UserId(actor.id),
        target_staff_id=ShopStaffId(staff.id),
        new_role=role,
        now=now,
    )
    _raise_for_staff_service_error(change_result, "Demo staff role update failed")


def _raise_for_staff_service_error(
    result: AddStaffResult | ChangeStaffRoleResult,
    message: str,
) -> None:
    if result.error is not None:
        raise CliError(message)


def _demo_shop_a_spec() -> DemoShopSpec:
    return DemoShopSpec(
        label="Demo Shop A",
        shop_id=DEMO_SHOP_A_ID,
        name=DEMO_SHOP_A_NAME,
        phone=DEMO_SHOP_A_PHONE,
        address_text=DEMO_SHOP_A_ADDRESS,
        owner_phone=DEMO_OWNER_A_PHONE,
    )


def _demo_shop_b_spec() -> DemoShopSpec:
    return DemoShopSpec(
        label="Demo Shop B",
        shop_id=DEMO_SHOP_B_ID,
        name=DEMO_SHOP_B_NAME,
        phone=DEMO_SHOP_B_PHONE,
        address_text=DEMO_SHOP_B_ADDRESS,
        owner_phone=DEMO_OWNER_B_PHONE,
    )


def _normalize_cli_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    normalized_reason = reason.strip()
    if not normalized_reason:
        return None
    return normalized_reason


def _storage_batch_size(value: str) -> int:
    try:
        batch_size = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(
            "storage batch size must be an integer"
        ) from None
    if batch_size < 1 or batch_size > 5000:
        raise argparse.ArgumentTypeError(
            "storage batch size must be between 1 and 5000"
        )
    return batch_size


def _nonzero_uuid(value: str) -> UUID:
    try:
        parsed = UUID(value)
    except ValueError:
        raise argparse.ArgumentTypeError("object id must be a UUID") from None
    if parsed.int == 0:
        raise argparse.ArgumentTypeError("object id must be a non-zero UUID")
    return parsed


def _configure_storage_service(
    settings: Settings,
) -> tuple[ObjectStorageService, BucketName, Callable[[], None]] | None:
    try:
        config = settings.require_object_storage_config()
        client = create_s3_client(config)
        storage = S3ObjectStorageService(client)
        bucket = BucketName(config.bucket)
    except (ObjectStorageSettingsError, StorageProviderError, ValueError):
        print("STORAGE_CONFIGURATION_UNAVAILABLE", file=sys.stderr)
        return None
    return storage, bucket, client.close


def _close_storage_connection(close_storage: Callable[[], None]) -> None:
    try:
        close_storage()
    except Exception:
        pass


def _format_storage_reconcile_result(
    upload: StorageReconcileResult,
    delete: StorageDeleteBatchResult,
) -> str:
    safe_codes = tuple(code.value for code in (*upload.safe_codes, *delete.safe_codes))
    rendered_codes = ",".join(safe_codes) if safe_codes else "NONE"
    return (
        "STORAGE_RECONCILE_OK "
        f"upload_claimed={upload.claimed_count} "
        f"available={upload.available_count} "
        f"failed={upload.failed_count} "
        f"pending={upload.pending_count} "
        f"deleted={upload.deleted_count} "
        f"delete_pending={upload.delete_pending_count} "
        f"delete_claimed={delete.claimed_count} "
        f"delete_completed={delete.deleted_count} "
        f"delete_unresolved={delete.pending_count} "
        f"codes={rendered_codes}"
    )


def _print_shop_create_error(error: ProvisionActiveShopError) -> int:
    if error is ProvisionActiveShopError.INVALID_NAME:
        print("Invalid shop name", file=sys.stderr)
        return 2
    if error is ProvisionActiveShopError.INVALID_PHONE:
        print("Invalid shop phone number", file=sys.stderr)
        return 2
    if error is ProvisionActiveShopError.OWNER_NOT_FOUND:
        print("Owner user not found", file=sys.stderr)
        return 2
    print("Shop creation failed", file=sys.stderr)
    return 1


def _print_shop_transition_error(error: ErrorCode) -> int:
    if error is ErrorCode.REASON_REQUIRED:
        print("Reason is required", file=sys.stderr)
        return 2
    if error is ErrorCode.FORBIDDEN:
        print("Shop not found or transition not allowed", file=sys.stderr)
        return 2
    print("Shop transition failed", file=sys.stderr)
    return 1


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
        if args.command == "bootstrap-platform-admin":
            return bootstrap_platform_admin(args, effective_settings)
        if args.command == "shop":
            if args.shop_command == "create":
                return create_shop(args, effective_settings)
            if args.shop_command == "suspend":
                return suspend_shop_command(args, effective_settings)
            if args.shop_command == "reactivate":
                return reactivate_shop_command(args, effective_settings)
        if args.command == "demo" and args.demo_command == "seed":
            return seed_demo(args, effective_settings)
        if args.command == "storage":
            if args.storage_command in {"preflight", "health"}:
                return storage_preflight_command(args, effective_settings)
            if args.storage_command == "reconcile":
                return storage_reconcile_command(args, effective_settings)
            if args.storage_command == "delete":
                return storage_delete_command(args, effective_settings)
            if args.storage_command == "smoke":
                return storage_smoke_command(args, effective_settings)
    except CliError as exc:
        print(str(exc), file=stderr or sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
