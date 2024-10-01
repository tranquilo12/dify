import os
import subprocess
from typing import List

import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.http import models as qrest
from qdrant_client.http.exceptions import UnexpectedResponse
from tqdm.auto import tqdm

from utils import (
    get_embeddings,
    initialize_qdrant,
    process_repositories,
    setup_tree_sitter_py,
    setup_tree_sitter_ts,
    split_code_chunk,
    store_chunks_multi,
)

VECTOR_SIZE = 1024
app = FastAPI()

REPO_CONFIGS = {
    "IntoTheDeep"     : {
        "path"    : "/volumes/IntoTheDeep",
        "language": "python",
        "parser"  : setup_tree_sitter_py()
    },
    "officeAddOn"     : {
        "path"    : "/volumes/officeAddOn",
        "language": "typescript",
        "parser"  : setup_tree_sitter_ts()
    },
    "LLMStuff"        : {
        "path"    : "/volumes/LLMStuff",
        "language": "python",
        "parser"  : setup_tree_sitter_py()
    },
    "dify": {
        "path"    : "/volumes/dify",
        "language": "python",
        "parser"  : setup_tree_sitter_py()
    },
    # Add more repositories as needed
}

# Initialize components
qclient: QdrantClient = initialize_qdrant()


class Query(BaseModel):
    text: str
    collection_name: str  # The project name


class SearchResult(BaseModel):
    file_path: str
    code: str
    chunk_type: str
    similarity: float


class CommitMsgRequest(BaseModel):
    repo_name: str


def get_git_diff(repo_path: str) -> str:
    try:
        # Change to the repository directory
        os.chdir(repo_path)

        # Run 'git diff' command
        result = subprocess.run(['git', 'diff'], capture_output=True, text=True, check=True)

        # Return the output
        return result.stdout
    except subprocess.CalledProcessError as e:
        # If the command fails, return the error message
        return f"Error: {e.stderr}"
    except Exception as e:
        # For any other exception, return a generic error message
        return f"An error occurred: {str(e)}"


@app.post("/git-diff")
async def generate_commit_msg(request: CommitMsgRequest):
    if request.repo_name not in REPO_CONFIGS:
        raise HTTPException(status_code=400, detail="Invalid repository name")

    repo_path = REPO_CONFIGS[request.repo_name]["path"]
    git_diff = get_git_diff(repo_path)

    if git_diff.startswith("Error:") or git_diff.startswith("An error occurred:"):
        raise HTTPException(status_code=500, detail=git_diff)

    # For now, we'll just return the diff
    return {"diff": git_diff}


@app.post("/index")
async def index_codebases():
    messages = []
    all_chunks = process_repositories(REPO_CONFIGS)
    collection_names = list(all_chunks.keys())

    # Create collection with vector configuration
    for col in collection_names:
        try:
            qclient.create_collection(
                collection_name=col,
                vectors_config={
                    'custom_vector': qrest.VectorParams(
                        distance=qrest.Distance.COSINE,
                        size=VECTOR_SIZE,
                    ),
                }
            )
        except UnexpectedResponse:
            # Collection already exists, continue
            pass

    for collection_name, chunks in tqdm(all_chunks.items(), total=len(all_chunks), desc="Collections..."):
        processed_chunks = []
        for chunk in tqdm(chunks, desc="Chunks..."):
            if len(str(chunk.content)) > 0:
                split_chunks = split_code_chunk(chunk)
                processed_chunks.extend(split_chunks)

        if not processed_chunks:
            messages.append(f"No valid chunks found for collection {collection_name}")
            continue

        chunk_contents = [chunk.content for chunk in processed_chunks]

        max_retries = 3
        retry_count = 0
        unprocessed_batches = None
        while retry_count < max_retries:
            embeddings, unprocessed_batches = get_embeddings(texts=chunk_contents, batch_size=5)

            if embeddings:
                try:
                    store_chunks_multi(
                        qclient,
                        collection_name,
                        processed_chunks[:len(embeddings)],
                        embeddings
                    )
                except Exception as e:
                    messages.append(f"Error storing embeddings for collection `{collection_name}`: {str(e)}")
                    break  # Exit the retry loop if storing fails

            if not unprocessed_batches:
                messages.append(f"Collection `{collection_name}` indexed successfully")
                break  # Exit the retry loop if all batches are processed

            # Prepare for next retry
            chunk_contents = [item for batch in unprocessed_batches for item in batch]
            processed_chunks = [chunk for chunk in processed_chunks if chunk.content in chunk_contents]
            retry_count += 1

        if retry_count == max_retries and unprocessed_batches:
            messages.append(
                f"Failed to process all batches for collection `{collection_name}` after {max_retries} retries"
            )

    return {"message": messages}


@app.post("/search", response_model=List[SearchResult])
async def search(query: Query):
    results = []
    if query.collection_name not in REPO_CONFIGS:
        raise HTTPException(status_code=400, detail="Invalid collection name")

    query_embedding, unprocessed_batches = get_embeddings(query.text)
    query_vector = qrest.NamedVector(name='custom_vector', vector=query_embedding[0].tolist())

    print(f"{query.collection_name=}")

    search_results = qclient.search(
        collection_name=query.collection_name,
        # query_vector=('custom_vector', query_embedding[0].tolist()),  # type: ignore
        query_vector=query_vector,
        limit=5,
        with_payload=True,
    )

    for hit in search_results:
        # Add an extra check to ensure the result is from the correct repository
        if hit.payload["file_path"].startswith(REPO_CONFIGS[query.collection_name]["path"]):
            results.append(
                SearchResult(
                    file_path=hit.payload["file_path"],
                    code=hit.payload["content"],
                    chunk_type=hit.payload["chunk_type"],
                    similarity=hit.score,
                )
            )
        else:
            print(f"Warning: Filtered out result from unexpected path: {hit.payload['file_path']}")

    return results


@app.get("/collections")
async def list_collections():
    return {"collections": list(REPO_CONFIGS.keys())}


if __name__ == "__main__":
    uvicorn.run("app:app", port=7779)