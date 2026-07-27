from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from app import cli
from app.auth.models import User
from app.db import create_database_session_factory
from app.settings import Settings
from app.shop.enums import ShopRole, ShopStaffAction, ShopStatus, ShopStatusAction
from app.shop.models import Shop, ShopStaff, ShopStaffEvent, ShopStatusEvent

TEST_RATE_LIMIT_HMAC_KEY = "test-rate-limit-hmac-key-for-shop-cli"


def make_settings(
    database_url: str,
    app_environment: str = "development",
) -> Settings:
    return Settings(
        _env_file=None,
        app_environment=app_environment,
        debug=False,
        database_url=database_url,
        session_cookie_secure=app_environment == "production",
        rate_limit_hmac_key=TEST_RATE_LIMIT_HMAC_KEY,
    )


def test_shop_cli_uses_services_without_direct_shop_queries() -> None:
    source = Path("app/cli.py").read_text()

    assert "from app.shop.models" not in source
    assert "exec_driver_sql" not in source
    for sql_fragment in ("select(", "insert(", "update(", "delete("):
        assert sql_fragment not in source


@pytest.mark.integration
def test_shop_create_uses_new_uuid_and_existing_owner(
    m2_test_database: Engine,
    test_database_url: str,
    capsys,
) -> None:
    settings = make_settings(test_database_url)
    owner_id = add_user(m2_test_database, phone="+998901234567")

    first_exit = cli.main(
        [
            "shop",
            "create",
            "--name",
            "First CLI Shop",
            "--phone",
            "90 111-22-33",
            "--owner-phone",
            "901234567",
        ],
        settings=settings,
    )
    first_output = capsys.readouterr()
    second_exit = cli.main(
        [
            "shop",
            "create",
            "--name",
            "Second CLI Shop",
            "--phone",
            "+998901112234",
            "--owner-phone",
            "+998901234567",
        ],
        settings=settings,
    )
    second_output = capsys.readouterr()

    assert first_exit == 0
    assert second_exit == 0
    first_shop_id = extract_created_shop_id(first_output.out)
    second_shop_id = extract_created_shop_id(second_output.out)
    assert first_shop_id != second_shop_id
    assert table_counts(m2_test_database) == {
        "users": 1,
        "shops": 2,
        "shop_staff": 2,
        "shop_status_events": 2,
        "shop_staff_events": 2,
    }
    assert active_staff_roles(m2_test_database) == {
        (first_shop_id, "+998901234567", ShopRole.OWNER.value),
        (second_shop_id, "+998901234567", ShopRole.OWNER.value),
    }
    assert owner_id is not None


@pytest.mark.integration
def test_shop_suspend_and_reactivate_commands_call_transition_services(
    m2_test_database: Engine,
    test_database_url: str,
    capsys,
) -> None:
    settings = make_settings(test_database_url)
    shop_id = add_shop(m2_test_database, status=ShopStatus.ACTIVE)

    suspend_exit = cli.main(
        ["shop", "suspend", str(shop_id), "--reason", "manual hold"],
        settings=settings,
    )
    suspend_output = capsys.readouterr()
    reactivate_exit = cli.main(
        ["shop", "reactivate", str(shop_id), "--reason", "review passed"],
        settings=settings,
    )
    reactivate_output = capsys.readouterr()

    assert suspend_exit == 0
    assert reactivate_exit == 0
    assert str(shop_id) in suspend_output.out
    assert str(shop_id) in reactivate_output.out
    assert shop_status(m2_test_database, shop_id) == ShopStatus.ACTIVE.value
    assert status_event_actions(m2_test_database) == [
        ShopStatusAction.SUSPENDED.value,
        ShopStatusAction.REACTIVATED.value,
    ]


@pytest.mark.integration
def test_shop_transition_blank_reason_is_rejected_by_cli_without_mutation(
    m2_test_database: Engine,
    test_database_url: str,
    capsys,
) -> None:
    settings = make_settings(test_database_url)
    shop_id = add_shop(m2_test_database, status=ShopStatus.ACTIVE)

    exit_code = cli.main(
        ["shop", "suspend", str(shop_id), "--reason", "   "],
        settings=settings,
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Reason is required" in captured.err
    assert shop_status(m2_test_database, shop_id) == ShopStatus.ACTIVE.value
    assert table_counts(m2_test_database)["shop_status_events"] == 0


@pytest.mark.parametrize(
    "argv",
    [
        [
            "shop",
            "create",
            "--name",
            "Blocked Shop",
            "--phone",
            "+998901111111",
            "--owner-phone",
            "+998901234567",
        ],
        ["shop", "suspend", str(uuid4()), "--reason", "blocked"],
        ["shop", "reactivate", str(uuid4()), "--reason", "blocked"],
        ["demo", "seed"],
    ],
)
@pytest.mark.integration
def test_shop_development_commands_fail_closed_in_production(
    m2_test_database: Engine,
    test_database_url: str,
    argv: list[str],
    capsys,
) -> None:
    settings = make_settings(test_database_url, app_environment="production")

    exit_code = cli.main(argv, settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "local development" in captured.err
    assert table_counts(m2_test_database) == {
        "users": 0,
        "shops": 0,
        "shop_staff": 0,
        "shop_status_events": 0,
        "shop_staff_events": 0,
    }


@pytest.mark.integration
def test_demo_seed_first_run_creates_expected_state_and_second_run_is_idempotent(
    m2_test_database: Engine,
    test_database_url: str,
    capsys,
) -> None:
    settings = make_settings(test_database_url)

    first_exit = cli.main(["demo", "seed"], settings=settings)
    first_output = capsys.readouterr()
    counts_after_first = table_counts(m2_test_database)
    staff_after_first = active_staff_roles(m2_test_database)
    events_after_first = staff_event_actions(m2_test_database)

    second_exit = cli.main(["demo", "seed"], settings=settings)
    second_output = capsys.readouterr()

    assert first_exit == 0
    assert second_exit == 0
    assert str(cli.DEMO_SHOP_A_ID) in first_output.out
    assert str(cli.DEMO_SHOP_B_ID) in first_output.out
    assert str(cli.DEMO_SHOP_A_ID) in second_output.out
    assert str(cli.DEMO_SHOP_B_ID) in second_output.out
    assert counts_after_first == {
        "users": 4,
        "shops": 2,
        "shop_staff": 5,
        "shop_status_events": 2,
        "shop_staff_events": 5,
    }
    assert table_counts(m2_test_database) == counts_after_first
    assert active_staff_roles(m2_test_database) == staff_after_first
    assert staff_event_actions(m2_test_database) == events_after_first
    assert len(events_after_first) == 5
    assert set(events_after_first) == {ShopStaffAction.ADDED.value}
    assert status_event_actions(m2_test_database) == [
        ShopStatusAction.ACTIVATED.value,
        ShopStatusAction.ACTIVATED.value,
    ]
    assert staff_after_first == {
        (cli.DEMO_SHOP_A_ID, cli.DEMO_OWNER_A_PHONE, ShopRole.OWNER.value),
        (cli.DEMO_SHOP_A_ID, cli.DEMO_MANAGER_A_PHONE, ShopRole.MANAGER.value),
        (cli.DEMO_SHOP_A_ID, cli.DEMO_CASHIER_A_PHONE, ShopRole.CASHIER.value),
        (cli.DEMO_SHOP_B_ID, cli.DEMO_OWNER_B_PHONE, ShopRole.OWNER.value),
        (cli.DEMO_SHOP_B_ID, cli.DEMO_OWNER_A_PHONE, ShopRole.MANAGER.value),
    }
    assert {row for row in staff_after_first if row[1] == cli.DEMO_OWNER_A_PHONE} == {
        (cli.DEMO_SHOP_A_ID, cli.DEMO_OWNER_A_PHONE, ShopRole.OWNER.value),
        (cli.DEMO_SHOP_B_ID, cli.DEMO_OWNER_A_PHONE, ShopRole.MANAGER.value),
    }


@pytest.mark.integration
def test_demo_seed_uses_fixed_uuid_even_when_another_shop_has_same_name(
    m2_test_database: Engine,
    test_database_url: str,
) -> None:
    settings = make_settings(test_database_url)
    decoy_id = add_shop(
        m2_test_database,
        name=cli.DEMO_SHOP_A_NAME,
        phone=cli.DEMO_SHOP_A_PHONE,
    )

    exit_code = cli.main(["demo", "seed"], settings=settings)

    assert exit_code == 0
    assert table_counts(m2_test_database)["shops"] == 3
    assert shop_identity(m2_test_database, cli.DEMO_SHOP_A_ID) == (
        cli.DEMO_SHOP_A_NAME,
        cli.DEMO_SHOP_A_PHONE,
        ShopStatus.ACTIVE.value,
    )
    assert shop_identity(m2_test_database, decoy_id) == (
        cli.DEMO_SHOP_A_NAME,
        cli.DEMO_SHOP_A_PHONE,
        ShopStatus.ACTIVE.value,
    )


@pytest.mark.integration
def test_demo_seed_fixed_uuid_conflict_fails_closed_without_mutation(
    m2_test_database: Engine,
    test_database_url: str,
    capsys,
) -> None:
    settings = make_settings(test_database_url)
    add_shop(
        m2_test_database,
        shop_id=cli.DEMO_SHOP_A_ID,
        name="Real Existing Shop",
        phone="+998900009999",
    )
    counts_before = table_counts(m2_test_database)
    identity_before = shop_identity(m2_test_database, cli.DEMO_SHOP_A_ID)

    exit_code = cli.main(["demo", "seed"], settings=settings)

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "unexpected shop" in captured.err
    assert "aborting without mutation" in captured.err
    assert table_counts(m2_test_database) == counts_before
    assert shop_identity(m2_test_database, cli.DEMO_SHOP_A_ID) == identity_before


def add_user(engine: Engine, *, phone: str) -> UUID:
    with create_database_session_factory(engine)() as session:
        user = User(phone=phone)
        session.add(user)
        session.flush()
        user_id = user.id
        session.commit()
        return user_id


def add_shop(
    engine: Engine,
    *,
    shop_id: UUID | None = None,
    name: str = "CLI Existing Shop",
    phone: str = "+998901119999",
    status: ShopStatus = ShopStatus.ACTIVE,
) -> UUID:
    with create_database_session_factory(engine)() as session:
        shop = Shop(
            id=shop_id or uuid4(),
            name=name,
            phone=phone,
            status=status.value,
        )
        session.add(shop)
        session.flush()
        created_shop_id = shop.id
        session.commit()
        return created_shop_id


def extract_created_shop_id(output: str) -> UUID:
    prefix = "Shop created: "
    assert prefix in output
    return UUID(output.split(prefix, maxsplit=1)[1].strip())


def table_counts(engine: Engine) -> dict[str, int]:
    with create_database_session_factory(engine)() as session:
        return {
            "users": count_rows(session, User),
            "shops": count_rows(session, Shop),
            "shop_staff": count_rows(session, ShopStaff),
            "shop_status_events": count_rows(session, ShopStatusEvent),
            "shop_staff_events": count_rows(session, ShopStaffEvent),
        }


def count_rows(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def shop_status(engine: Engine, shop_id: UUID) -> str:
    with create_database_session_factory(engine)() as session:
        return session.scalar(select(Shop.status).where(Shop.id == shop_id))


def shop_identity(engine: Engine, shop_id: UUID) -> tuple[str, str, str]:
    with create_database_session_factory(engine)() as session:
        row = session.execute(
            select(Shop.name, Shop.phone, Shop.status).where(Shop.id == shop_id)
        ).one()
        return row[0], row[1], row[2]


def active_staff_roles(engine: Engine) -> set[tuple[UUID, str, str]]:
    with create_database_session_factory(engine)() as session:
        rows = session.execute(
            select(ShopStaff.shop_id, User.phone, ShopStaff.role)
            .join(User, User.id == ShopStaff.user_id)
            .where(ShopStaff.is_active.is_(True))
            .order_by(ShopStaff.shop_id, User.phone)
        )
        return {(shop_id, phone, role) for shop_id, phone, role in rows}


def status_event_actions(engine: Engine) -> list[str]:
    with create_database_session_factory(engine)() as session:
        return list(
            session.scalars(
                select(ShopStatusEvent.action).order_by(ShopStatusEvent.created_at)
            )
        )


def staff_event_actions(engine: Engine) -> list[str]:
    with create_database_session_factory(engine)() as session:
        return list(
            session.scalars(
                select(ShopStaffEvent.action).order_by(ShopStaffEvent.created_at)
            )
        )
