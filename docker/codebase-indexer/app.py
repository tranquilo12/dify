import asyncio
import os
import subprocess
from contextlib import asynccontextmanager
from typing import List

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from qdrant_client import QdrantClient
from qdrant_client.http import models as qrest
from qdrant_client.http.exceptions import UnexpectedResponse

from git_operations import GitRepo, save_last_indexed_commit
from utils import (
    get_embeddings, initialize_qdrant, logger, process_repositories, setup_tree_sitter_py, setup_tree_sitter_ts,
    split_code_chunk, store_chunks_multi,
)

VECTOR_SIZE = 1024


async def check_and_index_repositories():
    while True:
        try:
            logger.info("Starting periodic indexing")
            await run_in_threadpool(index_codebases)
            logger.info("Finished periodic indexing")
        except Exception as e:
            logger.error(f"Error during periodic indexing: {str(e)}")
        await asyncio.sleep(300)  # Check every 10 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    background_tasks = BackgroundTasks()
    background_tasks.add_task(check_and_index_repositories)
    logger.info("Started background indexing task")
    yield


app = FastAPI(lifespan=lifespan)

REPO_CONFIGS = {
    "IntoTheDeep": {
        "path"    : "/volumes/IntoTheDeep",
        "language": "python",
        "parser"  : setup_tree_sitter_py()
    },
    "officeAddOn": {
        "path"    : "/volumes/officeAddOn",
        "language": "typescript",
        "parser"  : setup_tree_sitter_ts()
    },
    "LLMStuff"   : {
        "path"    : "/volumes/LLMStuff",
        "language": "python",
        "parser"  : setup_tree_sitter_py()
    },
    "dify"       : {
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
    try:
        logger.info("Manual indexing triggered")
        messages = []
        all_chunks = await process_repositories(REPO_CONFIGS)
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

        for collection_name, chunks in all_chunks.items():
            if not chunks:
                messages.append(f"No new changes to index for collection {collection_name}")
                continue

            processed_chunks = []
            for chunk in chunks:
                if len(str(chunk.content)) > 0:
                    split_chunks = split_code_chunk(chunk)
                    processed_chunks.extend(split_chunks)

            chunk_contents = [chunk.content for chunk in processed_chunks]

            embeddings, _ = get_embeddings(texts=chunk_contents, batch_size=5)

            if embeddings:
                try:
                    store_chunks_multi(
                        qclient,
                        collection_name,
                        processed_chunks[:len(embeddings)],
                        embeddings
                    )
                    messages.append(f"Collection `{collection_name}` indexed successfully")
                except Exception as e:
                    messages.append(f"Error storing embeddings for collection `{collection_name}`: {str(e)}")
            else:
                messages.append(f"No embeddings generated for collection `{collection_name}`")

        logger.info("Manual indexing completed")
        return {"message": messages}
    except Exception as e:
        logger.error(f"Error during manual indexing: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/force-index/{repo_name}")
async def force_index(repo_name: str):
    try:
        if repo_name not in REPO_CONFIGS:
            raise HTTPException(status_code=400, detail=f"Invalid repository name: {repo_name}")

        config = REPO_CONFIGS[repo_name]
        repo_path = config['path']
        language = config['language']
        parser = config['parser']

        git_repo = GitRepo(repo_path)

        # Force re-indexing by setting last_indexed_commit to None
        all_chunks = await process_repositories({repo_name: config}, force=True)

        messages = []
        if not all_chunks.get(repo_name):
            messages.append(f"No chunks to index for repository {repo_name}")
            return {"message": messages}

        try:
            # Ensure collection exists
            try:
                qclient.create_collection(
                    collection_name=repo_name,
                    vectors_config={
                        'custom_vector': qrest.VectorParams(
                            distance=qrest.Distance.COSINE,
                            size=VECTOR_SIZE,
                        ),
                    }
                )
            except Exception:  # type: ignore
                # Collection might already exist, continue
                pass

            processed_chunks = []
            for chunk in all_chunks[repo_name]:
                if len(str(chunk.content)) > 0:
                    split_chunks = split_code_chunk(chunk)
                    processed_chunks.extend(split_chunks)

            chunk_contents = [chunk.content for chunk in processed_chunks]

            embeddings, _ = get_embeddings(texts=chunk_contents, batch_size=5)

            if embeddings:
                await store_chunks_multi(
                    qclient,
                    repo_name,
                    processed_chunks[:len(embeddings)],
                    embeddings
                )
                messages.append(f"Repository `{repo_name}` indexed successfully")

                # Save the new last indexed commit
                save_last_indexed_commit(repo_path, git_repo.get_latest_commit())
            else:
                messages.append(f"No embeddings generated for repository `{repo_name}`")

        except Exception as e:
            messages.append(f"Error indexing repository `{repo_name}`: {str(e)}")

        return {"message": messages}
    except Exception as e:
        logger.error(f"Error during force indexing of {repo_name}: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


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