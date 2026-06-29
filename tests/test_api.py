import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

# Will fail here on first run
from api.main import app

client = TestClient(app)

@patch("api.main.ingest_and_compile_task")
def test_ingest_endpoint(mock_task):
    response = client.post(
        "/api/v1/ingest",
        files={"file": ("test.txt", b"Mock document content", "text/plain")}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "processing"
    assert "doc_id" in data
    assert mock_task.called

@patch("api.main.search")
@patch("api.main.get_llm_client")
def test_search_endpoint(mock_get_client, mock_search):
    mock_search.return_value = "Mock LLM/Search Answer"
    response = client.post(
        "/api/v1/search",
        json={"query": "test query"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["answer"] == "Mock LLM/Search Answer"
    assert mock_search.called

@patch("api.main.Linter")
def test_lint_endpoint(mock_linter_cls):
    mock_linter_instance = mock_linter_cls.return_value
    
    response = client.post("/api/v1/lint")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert mock_linter_instance.run_lint.called
