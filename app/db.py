from collections.abc import Callable, Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import Settings


class Base(DeclarativeBase):
    pass


def create_database_engine(settings: Settings) -> Engine:
    return create_engine(settings.database_url)


def create_database_session_factory(engine: Engine):
    return sessionmaker(bind=engine, class_=Session)


def create_database_session_dependency(
    session_factory: sessionmaker[Session],
) -> Callable[[], Generator[Session, None, None]]:
    def get_database_session() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return get_database_session
