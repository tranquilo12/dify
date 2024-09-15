import json
import os
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, field_validator
from qdrant_client import QdrantClient
from qdrant_client.http import models

from utils import (
    get_embeddings, initialize_qdrant, process_repositories, setup_tree_sitter_py, setup_tree_sitter_ts,
    store_chunks_multi,
)

app = FastAPI()

# Load REPO_CONFIGS from file if it exists, otherwise initialize as empty dict
REPO_CONFIGS_FILE = "repo_configs.json"
if os.path.exists(REPO_CONFIGS_FILE):
    with open(REPO_CONFIGS_FILE, "r") as f:
        REPO_CONFIGS: Dict[str, Dict[str, Any]] = json.load(f)
else:
    REPO_CONFIGS: Dict[str, Dict[str, Any]] = {}

# Initialize components
qclient: QdrantClient = initialize_qdrant(collection_names=list(REPO_CONFIGS.keys()))


class Query(BaseModel):
    text: str
    collection_name: str

    @field_validator('text')
    def text_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Query text must not be empty')
        return v


class SearchResult(BaseModel):
    file_path: str
    code: str
    chunk_type: str
    similarity: float


class RepositoryAction(BaseModel):
    action: str
    repo_name: str
    repo_path: str = ""
    language: str = ""

    @field_validator('action')
    def action_must_be_valid(cls, v: str) -> str:
        if v not in ["add", "remove"]:
            raise ValueError('Action must be either "add" or "remove"')
        return v

    @field_validator('repo_name')
    def repo_name_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError('Repository name must not be empty')
        return v


class ReindexRequest(BaseModel):
    repo_name: str

    @field_validator('repo_name')
    def repo_name_must_not_be_empty(cls, v):
        if not v.strip():
            raise ValueError('Repository name must not be empty')
        return v


def save_repo_configs():
    try:
        with open(REPO_CONFIGS_FILE, "w") as f:
            json.dump(REPO_CONFIGS, f)
    except TypeError as e:
        print(f"Error saving repo configs: {e}")
        # Optionally, you can log this error or handle it in a way that fits your application's needs


@app.post("/repositories")
async def manage_repository(action: RepositoryAction):
    if action.action == "add":
        if action.repo_name in REPO_CONFIGS:
            raise HTTPException(status_code=400, detail="Repository already exists")

        if not os.path.exists(action.repo_path):
            raise HTTPException(status_code=400, detail="Repository path does not exist")

        REPO_CONFIGS[action.repo_name] = {
            "path"    : action.repo_path,
            "language": action.language,
        }

        try:
            qclient.create_collection(
                collection_name=action.repo_name,
                vectors_config=models.VectorParams(size=1536, distance=models.Distance.COSINE),
            )
        except Exception as e:
            del REPO_CONFIGS[action.repo_name]
            raise HTTPException(status_code=500, detail=f"Failed to create collection: {str(e)}")

        try:
            # Run the indexing process in a separate thread
            await run_in_threadpool(index_repository, action.repo_name)
        except Exception as e:
            del REPO_CONFIGS[action.repo_name]
            qclient.delete_collection(collection_name=action.repo_name)
            raise HTTPException(status_code=500, detail=f"Failed to index repository: {str(e)}")

        save_repo_configs()
        return {"message": f"Repository '{action.repo_name}' added and indexed successfully"}

    elif action.action == "remove":
        if action.repo_name not in REPO_CONFIGS:
            raise HTTPException(status_code=404, detail="Repository not found")

        del REPO_CONFIGS[action.repo_name]
        try:
            qclient.delete_collection(collection_name=action.repo_name)
        except Exception as e:
            REPO_CONFIGS[action.repo_name] = action.repo_path  # Restore the config if deletion fails
            raise HTTPException(status_code=500, detail=f"Failed to delete collection: {str(e)}")

        save_repo_configs()
        return {"message": f"Repository '{action.repo_name}' removed successfully"}


@app.post("/reindex")
async def reindex_repository(request: ReindexRequest):
    if request.repo_name not in REPO_CONFIGS:
        raise HTTPException(status_code=404, detail="Repository not found")

    try:
        # Run the indexing process in a separate thread
        await run_in_threadpool(index_repository, request.repo_name)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to reindex repository: {str(e)}")

    return {"message": f"Repository '{request.repo_name}' reindexed successfully"}


@app.post("/search", response_model=List[SearchResult])
async def search(query: Query):
    if query.collection_name not in REPO_CONFIGS:
        raise HTTPException(status_code=400, detail="Invalid collection name")

    try:
        query_embedding = get_embeddings(query.text)
        search_results = qclient.search(
            collection_name=query.collection_name,
            query_vector=query_embedding[0],
            limit=5
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

    results = []
    for hit in search_results:
        results.append(
            SearchResult(
                file_path=hit.payload["file_path"],
                code=hit.payload["content"],
                chunk_type=hit.payload["chunk_type"],
                similarity=hit.score,
            )
        )

    return results


@app.get("/collections")
async def list_collections():
    return {"collections": list(REPO_CONFIGS.keys())}


def index_repository(repo_name: str):
    repo_config = REPO_CONFIGS[repo_name]
    language = repo_config["language"]
    parser = setup_tree_sitter_py() if language == "python" else setup_tree_sitter_ts()

    chunks = process_repositories({repo_name: {**repo_config, "parser": parser}})
    chunk_contents = [chunk.content for chunk in chunks[repo_name]]
    embs = get_embeddings(texts=chunk_contents, batch_size=32)

    # Clear existing data for the repository
    qclient.delete(collection_name=repo_name, points_selector=models.FilterSelector(filter=models.Filter()))

    # Store new chunks
    store_chunks_multi(
        client=qclient,
        collection_name=repo_name,
        chunks=chunks[repo_name],
        embeddings=embs
    )