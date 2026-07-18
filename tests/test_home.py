from fastapi.testclient import TestClient

from app.main import create_app


def test_home_returns_html() -> None:
    client = TestClient(create_app())

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Nasiya" in response.text
