from unittest.mock import patch

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine

from app.main import create_app


def test_create_app_prepares_database_dependency_without_connecting() -> None:
    with patch.object(Engine, "connect") as connect_mock:
        application = create_app()

    connect_mock.assert_not_called()
    assert application.state.database_engine is not None
    assert application.state.database_session_factory is not None
    assert callable(application.state.get_database_session)


def test_database_session_dependency_can_be_overridden() -> None:
    application = create_app()
    replacement_session = object()

    def override_database_session():
        yield replacement_session

    @application.get("/_test/database-session")
    def read_database_session(
        session: object = Depends(application.state.get_database_session),
    ) -> dict[str, bool]:
        return {"overridden": session is replacement_session}

    application.dependency_overrides[application.state.get_database_session] = (
        override_database_session
    )

    client = TestClient(application)
    response = client.get("/_test/database-session")

    assert response.status_code == 200
    assert response.json() == {"overridden": True}
