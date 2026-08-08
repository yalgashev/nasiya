from collections.abc import Callable, Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import Settings


class Base(DeclarativeBase):
    pass


def _register_database_model_dependencies() -> None:
    from app.customer_document import models as _customer_document_models  # noqa: F401
    from app.debt import models as _debt_models  # noqa: F401
    from app.idempotency import models as _idempotency_models  # noqa: F401
    from app.offers import models as _offer_models  # noqa: F401
    from app.payment import models as _payment_models  # noqa: F401
    from app.shop import models as _shop_models  # noqa: F401
    from app.shop_customer import models as _shop_customer_models  # noqa: F401
    from app.storage import models as _storage_models  # noqa: F401


def create_database_engine(settings: Settings) -> Engine:
    _register_database_model_dependencies()
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
