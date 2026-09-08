import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient
from pydantic import BaseModel, model_validator

from core.exceptions import register_exception_handlers


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    yield


class CheckedPayload(BaseModel):
    start: int
    end: int

    @model_validator(mode="after")
    def check_order(self):
        if self.end <= self.start:
            raise ValueError("end must be after start")
        return self


@pytest.fixture
def client():
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/checked")
    async def checked(body: CheckedPayload):
        return body

    @app.get("/binary-error")
    async def binary_error():
        raise RequestValidationError([{
            "type": "value_error", "loc": ("body",), "msg": "Invalid input",
            "input": bytes([255]), "ctx": {"error": ValueError("bad")},
        }])

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("payload", [{"start": 5, "end": 1}, {"start": "invalid", "end": 1}])
def test_validation_returns_422_with_serializable_errors(client, payload):
    response = client.post("/checked", json=payload)
    assert response.status_code == 422
    assert response.json()["code"] == 422
    assert set(response.json()["data"][0]) == {"type", "loc", "msg"}


def test_non_utf8_input_does_not_break_error_response(client):
    assert client.get("/binary-error").status_code == 422


def test_valid_payload_is_unchanged(client):
    assert client.post("/checked", json={"start": 1, "end": 5}).status_code == 200
