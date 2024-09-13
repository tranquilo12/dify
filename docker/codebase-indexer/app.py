from typing import List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from qdrant_client import QdrantClient

from utils import (
    get_embeddings, initialize_qdrant, process_repositories, setup_tree_sitter_py, setup_tree_sitter_ts,
    store_chunks_multi,
)

app = FastAPI()

REPO_CONFIGS = {
    "IntoTheDeep": {
        "path"    : "/app/codebase/IntoTheDeep",
        "language": "python",
        "parser"  : setup_tree_sitter_py()
    },
    "officeAddOn": {
        "path"    : "/app/codebase/officeAddOn",
        "language": "typescript",
        "parser"  : setup_tree_sitter_ts()
    },
    # Add more repositories as needed
}

# Initialize components
qclient: QdrantClient = initialize_qdrant(collection_names=list(REPO_CONFIGS.keys()))


class Query(BaseModel):
    text: str
    collection_name: str  # The project name


class SearchResult(BaseModel):
    file_path: str
    code: str
    chunk_type: str
    similarity: float


@app.post("/index")
async def index_codebases():
    messages = []
    all_chunks = process_repositories(REPO_CONFIGS)
    for collection_name, chunks in all_chunks.items():
        chunk_contents = [chunk.content for chunk in chunks]
        embs = get_embeddings(texts=chunk_contents, batch_size=32)
        store_chunks_multi(
            client=qclient,
            collection_name=collection_name,
            chunks=chunks,
            embeddings=embs
        )
        messages.append(f"Collection `{collection_name}` indexed successfully")
    return {"message": messages}


@app.post("/search", response_model=List[SearchResult])
async def search(query: Query):
    results = []
    if query.collection_name not in REPO_CONFIGS:
        raise HTTPException(status_code=400, detail="Invalid collection name")

    query_embedding = get_embeddings(query.text)
    search_results = qclient.query_points(
        collection_name=query.collection_name,
        query=query_embedding[0],
        limit=5
    )

    for typ, hit in search_results:
        for ele in hit:
            # Add an extra check to ensure the result is from the correct repository
            if ele.payload["file_path"].startswith(REPO_CONFIGS[query.collection_name]["path"]):
                results.append(
                    SearchResult(
                        file_path=ele.payload["file_path"],
                        code=ele.payload["content"],
                        chunk_type=ele.payload["chunk_type"],
                        similarity=ele.score,
                    )
                )
            else:
                print(f"Warning: Filtered out result from unexpected path: {ele.payload['file_path']}")

    return results


@app.get("/collections")
async def list_collections():
    return {"collections": list(REPO_CONFIGS.keys())}

