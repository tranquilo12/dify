import time

import pytest
import requests

# Configuration
API_URL = "http://localhost:7779"  # Adjust if your port is different
TEST_REPO_PATH = "/app/repos/test_repo"  # This should be a path accessible inside the Docker container
TEST_REPO_NAME = "test_repo"
TEST_REPO_LANGUAGE = "python"


@pytest.fixture(scope="module")
def setup_teardown():
    # Setup: Ensure the test repository doesn't exist
    requests.post(
        f"{API_URL}/repositories", json={
            "action"   : "remove",
            "repo_name": TEST_REPO_NAME
        }
    )

    yield  # This is where the testing happens

    # Teardown: Remove the test repository if it exists
    requests.post(
        f"{API_URL}/repositories", json={
            "action"   : "remove",
            "repo_name": TEST_REPO_NAME
        }
    )


def test_add_repository(setup_teardown):
    response = requests.post(
        f"{API_URL}/repositories", json={
            "action"   : "add",
            "repo_name": TEST_REPO_NAME,
            "repo_path": TEST_REPO_PATH,
            "language" : TEST_REPO_LANGUAGE
        }
    )
    assert response.status_code == 200, f"Failed to add repository: {response.text}"
    assert "added and indexed successfully" in response.json()["message"]


def test_list_repositories(setup_teardown):
    response = requests.get(f"{API_URL}/collections")
    assert response.status_code == 200, f"Failed to list repositories: {response.text}"
    collections = response.json()['collections']
    assert TEST_REPO_NAME in collections, f"Added repository not found in list: {collections}"


def test_search_repository(setup_teardown):
    response = requests.post(
        f"{API_URL}/search", json={
            "text"           : "example function",
            "collection_name": TEST_REPO_NAME
        }
    )
    assert response.status_code == 200, f"Search failed: {response.text}"
    search_results = response.json()
    assert isinstance(search_results, list), "Search results should be a list"
    # Add more specific assertions based on your expected search results


def test_reindex_repository(setup_teardown):
    # In a real scenario, you'd modify a file in the repo here
    # For this test, we'll just call the reindex endpoint directly
    time.sleep(6)  # Wait for the debounce period
    response = requests.post(f"{API_URL}/reindex", json={"repo_name": TEST_REPO_NAME})
    assert response.status_code == 200, f"Reindexing failed: {response.text}"
    assert "reindexed successfully" in response.json()["message"]


def test_remove_repository(setup_teardown):
    response = requests.post(
        f"{API_URL}/repositories", json={
            "action"   : "remove",
            "repo_name": TEST_REPO_NAME
        }
    )
    assert response.status_code == 200, f"Failed to remove repository: {response.text}"
    assert "removed successfully" in response.json()["message"]

    # Verify removal
    response = requests.get(f"{API_URL}/collections")
    assert response.status_code == 200, f"Failed to list repositories: {response.text}"
    collections = response.json()['collections']
    assert TEST_REPO_NAME not in collections, f"Removed repository still in list: {collections}"


def test_add_duplicate_repository(setup_teardown):
    # First, add the repository
    requests.post(
        f"{API_URL}/repositories", json={
            "action"   : "add",
            "repo_name": TEST_REPO_NAME,
            "repo_path": TEST_REPO_PATH,
            "language" : TEST_REPO_LANGUAGE
        }
    )

    # Try to add it again
    response = requests.post(
        f"{API_URL}/repositories", json={
            "action"   : "remove",
            "repo_name": TEST_REPO_NAME,
            "repo_path": TEST_REPO_PATH,
            "language" : TEST_REPO_LANGUAGE
        }
    )
    assert response.status_code == 400, "Adding a duplicate repository should fail"


def test_remove_nonexistent_repository(setup_teardown):
    response = requests.post(
        f"{API_URL}/repositories", json={
            "action"   : "remove",
            "repo_name": "nonexistent_repo"
        }
    )
    assert response.status_code == 404, "Removing a nonexistent repository should return 404"


def test_search_nonexistent_repository(setup_teardown):
    response = requests.post(
        f"{API_URL}/search", json={
            "text"           : "example function",
            "collection_name": "nonexistent_repo"
        }
    )
    assert response.status_code == 400, "Searching in a nonexistent repository should return 400"