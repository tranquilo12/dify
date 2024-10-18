from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from qdrant_client.http import models as qrest

from app import IndexingState, app

# Initialize test client
client = TestClient(app)


@pytest.fixture
def mock_qdrant_client():
    with patch("app.QdrantClient") as mock_client:
        yield mock_client


@pytest.fixture
def mock_git_repo():
    with patch("app.GitRepo") as mock_repo:
        yield mock_repo


@pytest.mark.asyncio
async def test_get_repositories(mock_qdrant_client):
    # Mock the scroll method of QdrantClient
    mock_scroll = AsyncMock()
    mock_scroll.return_value = ([
                                    qrest.ScoredPoint(id=1, version=1, score=1.0, payload={"repository": "repo1"}),
                                    qrest.ScoredPoint(id=2, version=1, score=1.0, payload={"repository": "repo2"}),
                                    qrest.ScoredPoint(id=3, version=1, score=1.0, payload={"repository": "repo1"}),
                                ], None)
    mock_qdrant_client.return_value.scroll = mock_scroll

    response = client.get("/repositories")
    assert response.status_code == 200
    # assert response.json() == {"repositories": ["repo1", "repo2"]}


@pytest.mark.asyncio
async def test_get_indexing_status():
    response = client.get("/indexing-status/ChatApp")
    assert response.status_code == 200
    assert "status" in response.json()
    assert "message" in response.json()
    assert "last_updated" in response.json()


@pytest.mark.asyncio
async def test_get_indexing_status_invalid_repo():
    response = client.get("/indexing-status/InvalidRepo")
    assert response.status_code == 400
    assert "detail" in response.json()


@pytest.mark.asyncio
async def test_index_repository(mock_git_repo, mock_qdrant_client):
    # Mock necessary methods
    mock_git_repo.return_value.get_latest_commit.return_value = "abc123"
    mock_qdrant_client.return_value.create_collection = MagicMock()

    with patch("app.get_embeddings") as mock_get_embeddings, \
            patch("app.store_chunks_multi") as mock_store_chunks, \
            patch("app.save_last_indexed_commit") as mock_save_commit, \
            patch("app.detect_changes") as mock_detect_changes, \
            patch("app.get_files_to_index") as mock_get_files, \
            patch("app.chunk_code_file") as mock_chunk_file, \
            patch("app.split_code_chunk") as mock_split_chunk:
        mock_detect_changes.return_value = True
        mock_get_files.return_value = ["file1.py", "file2.py"]
        mock_chunk_file.return_value = [MagicMock()]
        mock_split_chunk.return_value = [MagicMock()]
        mock_get_embeddings.return_value = ([MagicMock()], None)

        response = client.post("/index/ChatApp")
        assert response.status_code == 200
        assert response.json()["status"] == IndexingState.COMPLETED.value


@pytest.mark.asyncio
async def test_index_repository_error(mock_git_repo, mock_qdrant_client):
    mock_git_repo.return_value.get_latest_commit.side_effect = Exception("Git error")

    with patch("app.logger.error"):  # Suppress logger.error output
        response = client.post("/index/ChatApp")
    assert response.status_code == 200
    assert response.json()["status"] == IndexingState.FAILED.value


@pytest.mark.asyncio
async def test_websocket_connection():
    with client.websocket_connect("/ws") as websocket:
        data = {"test": "message"}
        await websocket.send_json(data)
        # We're not expecting a response from the server for this message
        # Instead, let's trigger an indexing operation to get a response
        client.post("/index/ChatApp")
        response = await websocket.receive_json()
        assert "ChatApp" in response
        assert "status" in response["ChatApp"]


if __name__ == "__main__":
    pytest.main([__file__])