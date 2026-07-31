import inspect
from uuid import UUID

from app import cli


def test_cli_exposes_only_first_admin_bootstrap_command() -> None:
    user_id = UUID("11111111-1111-4111-8111-111111111111")

    args = cli.build_parser().parse_args(
        ["bootstrap-platform-admin", "--user-id", str(user_id)]
    )

    assert args.command == "bootstrap-platform-admin"
    assert args.user_id == user_id
    parser_source = inspect.getsource(cli.build_parser)
    assert parser_source.count('"bootstrap-platform-admin"') == 1
    assert "grant-platform-admin" not in parser_source
    assert "revoke-platform-admin" not in parser_source
    assert "create-platform-admin" not in parser_source


def test_cli_bootstrap_is_explicit_outer_transaction_coordinator() -> None:
    source = inspect.getsource(cli.bootstrap_platform_admin)

    assert "session_factory.begin()" in source
    assert "bootstrap_first_platform_admin" in source
    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert "target_user_id=args.user_id" in source
