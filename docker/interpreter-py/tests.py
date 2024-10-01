import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_execute_simple_code():
    response = client.post("/execute", json={"code": "print('Hello, World!')"})
    assert response.status_code == 200
    assert response.json()["stdout"].strip() == "Hello, World!"
    assert response.json()["returncode"] == 0


def test_execute_code_with_error():
    response = client.post("/execute", json={"code": "print(undefined_variable)"})
    assert response.status_code == 200
    assert "NameError" in response.json()["stderr"]
    assert response.json()["returncode"] != 0


def test_execute_long_running_code():
    response = client.post("/execute", json={"code": "import time; time.sleep(40)"})
    assert response.status_code == 408
    assert response.json()["detail"] == "Execution timeout"


def test_execute_invalid_input():
    response = client.post("/execute", json={"invalid_key": "print('Hello')"})
    assert response.status_code == 422


def test_execute_code_with_imports():
    response = client.post("/execute", json={"code": "import math; print(math.pi)"})
    assert response.status_code == 200
    assert float(response.json()["stdout"].strip()) == pytest.approx(3.141592653589793)


if __name__ == "__main__":
    pytest.main([__file__])