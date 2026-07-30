import inspect
from collections.abc import Generator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

import app.storage.repository as storage_repository
from app.auth.models import User
from app.db import create_database_session_factory
from app.storage.contracts import (
    BucketName,
    ObjectChecksumSha256,
    ObjectKey,
    SanitizedImageMetadata,
)
from app.storage.models import ObjectFile, ObjectFileStatus
from app.storage.repository import (
    create_pending_object_file,
    load_available_object_file,
    load_object_file,
    load_object_file_for_update,
)

NOW = datetime(2026, 7, 30, 18, 0, tzinfo=UTC)


class SessionSpy:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.commit_called = False
        self.rollback_called = False
        self.close_called = False

    def add(self, *args, **kwargs):
        return self.session.add(*args, **kwargs)

    def flush(self, *args, **kwargs):
        return self.session.flush(*args, **kwargs)

    def scalar(self, *args, **kwargs):
        return self.session.scalar(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.session.get(*args, **kwargs)

    def commit(self) -> None:
        self.commit_called = True

    def rollback(self) -> None:
        self.rollback_called = True

    def close(self) -> None:
        self.close_called = True

    def __getattr__(self, name: str):
        return getattr(self.session, name)


@pytest.fixture
def db_session(m2_test_database: Engine) -> Generator[Session, None, None]:
    session_factory = create_database_session_factory(m2_test_database)
    session = session_factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _add_user(session: Session, *, phone: str = "+998900008321") -> User:
    user = User(phone=phone, created_at=NOW, updated_at=NOW)
    session.add(user)
    session.flush()
    return user


def _metadata() -> SanitizedImageMetadata:
    return SanitizedImageMetadata(
        content_type="image/png",
        canonical_extension="png",
        size_bytes=128,
        width_px=8,
        height_px=6,
        checksum_sha256=ObjectChecksumSha256("a" * 64),
    )


def _create_pending(
    session: Session,
    *,
    user_id: UUID,
    object_file_id: UUID | None = None,
) -> ObjectFile:
    resolved_id = object_file_id or uuid4()
    return create_pending_object_file(
        session,
        object_file_id=resolved_id,
        bucket=BucketName("nasiya-private-test"),
        object_key=ObjectKey(f"v1/objects/{resolved_id.hex}.png"),
        metadata=_metadata(),
        created_by_user_id=user_id,
        now=NOW,
    )


def test_repository_api_is_narrow_and_caller_owned() -> None:
    source = inspect.getsource(storage_repository)

    assert ".commit(" not in source
    assert ".rollback(" not in source
    assert ".close(" not in source
    assert "httpx" not in source
    assert "boto" not in source
    assert "Request" not in source
    assert "Response" not in source
    assert "logger" not in source
    assert "print(" not in source
    assert "filename" not in source
    assert "endpoint" not in source
    assert "credential" not in source
    assert "presigned" not in source

    public_functions = (
        create_pending_object_file,
        load_object_file,
        load_object_file_for_update,
        load_available_object_file,
    )
    for function in public_functions:
        parameters = list(inspect.signature(function).parameters.values())
        assert parameters[0].name == "session"
        assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in parameters[1:]
        )

    load_functions = public_functions[1:]
    for function in load_functions:
        assert set(inspect.signature(function).parameters) == {
            "session",
            "object_file_id",
        }


@pytest.mark.integration
def test_create_and_load_primitives_preserve_creator_and_pending_state(
    db_session: Session,
) -> None:
    creator = _add_user(db_session)
    other_user = _add_user(db_session, phone="+998900008322")
    object_file_id = uuid4()

    created = _create_pending(
        db_session,
        user_id=creator.id,
        object_file_id=object_file_id,
    )
    assert created.id == object_file_id
    assert created.created_by_user_id == creator.id
    assert created.created_by_user_id != other_user.id
    assert created.status == ObjectFileStatus.PENDING_UPLOAD.value
    assert created.failure_code is None
    assert created.available_at is None
    assert created.terminal_at is None
    assert created.deleted_at is None
    assert created.created_at == NOW
    assert created.updated_at == NOW

    db_session.expire_all()
    assert (
        load_object_file(
            db_session,
            object_file_id=object_file_id,
        ).created_by_user_id
        == creator.id
    )
    assert (
        load_available_object_file(
            db_session,
            object_file_id=object_file_id,
        )
        is None
    )
    assert (
        load_object_file(
            db_session,
            object_file_id=uuid4(),
        )
        is None
    )


@pytest.mark.integration
def test_create_requires_existing_creator(db_session: Session) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        with db_session.begin_nested():
            _create_pending(db_session, user_id=uuid4())

    assert exc_info.value.orig.diag.constraint_name == (
        "fk_object_files_created_by_user_id_users_id"
    )
    assert db_session.scalar(select(1)) == 1


@pytest.mark.integration
def test_load_available_returns_only_available_rows(db_session: Session) -> None:
    creator = _add_user(db_session)
    available = _create_pending(db_session, user_id=creator.id)
    pending = _create_pending(db_session, user_id=creator.id)
    available.status = ObjectFileStatus.AVAILABLE.value
    available.available_at = NOW
    db_session.flush()
    db_session.expire_all()

    assert (
        load_available_object_file(
            db_session,
            object_file_id=available.id,
        ).id
        == available.id
    )
    assert (
        load_available_object_file(
            db_session,
            object_file_id=pending.id,
        )
        is None
    )


@pytest.mark.integration
def test_load_for_update_takes_a_real_postgresql_row_lock(
    m2_test_database: Engine,
) -> None:
    session_factory = create_database_session_factory(m2_test_database)
    setup_session = session_factory()
    first_session = session_factory()
    second_session = session_factory()
    try:
        creator = _add_user(setup_session)
        object_file = _create_pending(setup_session, user_id=creator.id)
        object_file_id = object_file.id
        setup_session.commit()

        locked = load_object_file_for_update(
            first_session,
            object_file_id=object_file_id,
        )
        assert locked is not None
        assert locked.id == object_file_id

        with pytest.raises(OperationalError):
            second_session.scalar(
                select(ObjectFile)
                .where(ObjectFile.id == object_file_id)
                .with_for_update(nowait=True)
            )
        second_session.rollback()
        assert second_session.scalar(select(1)) == 1
    finally:
        setup_session.close()
        first_session.rollback()
        first_session.close()
        second_session.rollback()
        second_session.close()


@pytest.mark.integration
def test_repository_never_owns_commit_rollback_or_close(
    db_session: Session,
) -> None:
    creator = _add_user(db_session)
    session_spy = SessionSpy(db_session)
    object_file = _create_pending(session_spy, user_id=creator.id)

    assert (
        load_object_file(
            session_spy,
            object_file_id=object_file.id,
        )
        is object_file
    )
    assert (
        load_object_file_for_update(
            session_spy,
            object_file_id=object_file.id,
        )
        is object_file
    )
    assert (
        load_available_object_file(
            session_spy,
            object_file_id=object_file.id,
        )
        is None
    )
    assert session_spy.commit_called is False
    assert session_spy.rollback_called is False
    assert session_spy.close_called is False
