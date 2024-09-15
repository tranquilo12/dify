from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.http import models

from utils import (
    get_embeddings, initialize_qdrant, process_repositories, setup_tree_sitter_py, setup_tree_sitter_ts,
    store_chunks_multi,
)

app = FastAPI()

REPO_CONFIGS: Dict[str, Dict[str, Any]] = {}

# Initialize components
qclient: QdrantClient = initialize_qdrant(collection_names=[])


class Query(BaseModel):
    text: str
    collection_name: str


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


class ReindexRequest(BaseModel):
    repo_name: str


@app.post("/repositories")
async def manage_repository(action: RepositoryAction):
    if action.action == "add":
        if action.repo_name in REPO_CONFIGS:
            raise HTTPException(status_code=400, detail="Repository already exists")

        REPO_CONFIGS[action.repo_name] = {
            "path"    : action.repo_path,
            "language": action.language,
            "parser"  : setup_tree_sitter_py() if action.language == "python" else setup_tree_sitter_ts()
        }

        qclient.create_collection(
            collection_name=action.repo_name,
            vectors_config=models.VectorParams(size=1536, distance=models.Distance.COSINE),
        )

        # Index the new repository
        chunks = process_repositories({action.repo_name: REPO_CONFIGS[action.repo_name]})
        chunk_contents = [chunk.content for chunk in chunks[action.repo_name]]
        embs = get_embeddings(texts=chunk_contents, batch_size=32)
        store_chunks_multi(
            client=qclient,
            collection_name=action.repo_name,
            chunks=chunks[action.repo_name],
            embeddings=embs
        )

        return {"message": f"Repository '{action.repo_name}' added and indexed successfully"}

    elif action.action == "remove":
        if action.repo_name not in REPO_CONFIGS:
            raise HTTPException(status_code=404, detail="Repository not found")

        del REPO_CONFIGS[action.repo_name]
        qclient.delete_collection(collection_name=action.repo_name)

        return {"message": f"Repository '{action.repo_name}' removed successfully"}

    else:
        raise HTTPException(status_code=400, detail="Invalid action")


@app.post("/reindex")
async def reindex_repository(request: ReindexRequest):
    if request.repo_name not in REPO_CONFIGS:
        raise HTTPException(status_code=404, detail="Repository not found")

    # Process the repository
    chunks = process_repositories({request.repo_name: REPO_CONFIGS[request.repo_name]})
    chunk_contents = [chunk.content for chunk in chunks[request.repo_name]]
    embs = get_embeddings(texts=chunk_contents, batch_size=32)

    # Clear existing data for the repository
    qclient.delete(collection_name=request.repo_name, points_selector=models.FilterSelector(filter=models.Filter()))

    # Store new chunks
    store_chunks_multi(
        client=qclient,
        collection_name=request.repo_name,
        chunks=chunks[request.repo_name],
        embeddings=embs
    )

    return {"message": f"Repository '{request.repo_name}' reindexed successfully"}


@app.post("/search", response_model=List[SearchResult])
async def search(query: Query):
    if query.collection_name not in REPO_CONFIGS:
        raise HTTPException(status_code=400, detail="Invalid collection name")

    query_embedding = get_embeddings(query.text)
    search_results = qclient.search(
        collection_name=query.collection_name,
        query_vector=query_embedding[0],
        limit=5
    )

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