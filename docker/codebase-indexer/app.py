import os
import subprocess
import time
import traceback
from contextlib import asynccontextmanager
from enum import Enum
from typing import Dict, List, Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient

from git_operations import (
    GitRepo, detect_changes, load_last_indexed_commit, save_last_indexed_commit,
)
from utils import (
    chunk_code_file, ensure_collection_exists, get_embeddings, get_files_to_index, initialize_qdrant, logger,
    setup_tree_sitter_py, setup_tree_sitter_ts, split_code_chunk, store_chunks_multi,
)

VECTOR_SIZE = 1024

REPO_CONFIGS = {
    "erudite"    : {
        "path"    : "/volumes/erudite",
        "language": "typescript",
        "parser"  : setup_tree_sitter_ts()
    },
    "officeAddOn": {
        "path"    : "/volumes/officeAddOn",
        "language": "typescript",
        "parser"  : setup_tree_sitter_ts()
    },
    "dify"       : {
        "path"    : "/volumes/dify",
        "language": "python",
        "parser"  : setup_tree_sitter_py()
    },
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


class GitDiffRequest(BaseModel):
    repo_name: str
    from_commit: Optional[str] = None
    to_commit: Optional[str] = "HEAD"
    file_paths: Optional[List[str]] = None
    ignore_whitespace: bool = False
    context_lines: Optional[int] = None


class CommitMsgRequest(BaseModel):
    repo_name: str


class IndexingState(str, Enum):
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class IndexingStatus(BaseModel):
    status: IndexingState
    message: str
    last_updated: float


class DetailedIndexingStatus(BaseModel):
    status: IndexingState
    message: str
    last_updated: float
    files_to_index: List[str] = Field(default_factory=lambda: [])
    current_file: str = ""
    processed_files: List[str] = Field(default_factory=lambda: [])
    total_files: int = 0
    processed_count: int = 0

    def model_dump(self, **kwargs):
        data = super().model_dump(**kwargs)
        data['files_to_index'] = [str(f) for f in data['files_to_index']]
        data['current_file'] = str(data['current_file'])
        data['processed_files'] = [str(f) for f in data['processed_files']]
        return data


# Global variable to store detailed indexing status
detailed_indexing_statuses: Dict[str, DetailedIndexingStatus] = {
    repo_name: DetailedIndexingStatus(
        status=IndexingState.NOT_STARTED,
        message="Indexing not started",
        last_updated=time.time(),
        files_to_index=[],
        current_file="",
        processed_files=[],
        total_files=0,
        processed_count=0
    )
    for repo_name in REPO_CONFIGS.keys()
}


def get_git_diff(repo_path: str) -> str:
    try:
        os.chdir(repo_path)
        result = subprocess.run(['git', 'diff'], capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error: {e.stderr}"
    except Exception as e:
        return f"An error occurred: {str(e)}"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Application startup. WebSocket route registered at /ws")
    yield
    # Shutdown
    logger.info("Application shutdown")


async def trigger_indexing(repo_name: str):
    async with httpx.AsyncClient() as client:
        response = await client.post(f"http://localhost:7779/index/{repo_name}")
        return response.json()


# WebSocket connections
active_connections: List[WebSocket] = []

# Configure CORS after instantiating app
app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,  # type: ignore
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)


@app.websocket("/indexer")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "index":
                await index_repository(websocket, data)
            # TODO: Add endpoints for both refreshing and deleting the index
    except WebSocketDisconnect:
        active_connections.remove(websocket)


# Everything related to the endpoints
@app.get("/repos-in-qdrant")
async def get_repos_in_qdrant():
    try:
        # Assuming all chunks are stored in a collection named "code_chunks"
        scroll_result = qclient.scroll(
            collection_name="code_chunks",
            scroll_filter=None,
            limit=10000,  # Adjust based on expected number of chunks
            with_payload=["repository"],
            with_vectors=False,
        )

        # Extract unique repository names from the payload
        repositories = set()
        for point in scroll_result[0]:
            if "repository" in point.payload:
                repositories.add(point.payload["repository"])

        return {"repositories": list(repositories)}
    except Exception as e:
        logger.error(f"Error retrieving repositories: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to retrieve repositories")


@app.get("/repos-in-container")
def get_repos_in_container():
    volumes_dir = "/volumes"
    repositories = [d for d in os.listdir(volumes_dir) if os.path.isdir(os.path.join(volumes_dir, d))]
    return {"repositories": repositories}


def get_parameterized_git_diff(repo_path: str, options: GitDiffRequest) -> str:
    try:
        os.chdir(repo_path)
        cmd = ['git', 'diff']

        if options.ignore_whitespace:
            cmd.append('--ignore-all-space')

        if options.context_lines is not None:
            cmd.extend([f'-U{options.context_lines}'])

        if options.from_commit:
            cmd.append(options.from_commit)

        if options.to_commit:
            cmd.append(options.to_commit)

        if options.file_paths:
            cmd.extend(['--'] + options.file_paths)

        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout
    except subprocess.CalledProcessError as e:
        return f"Error: {e.stderr}"
    except Exception as e:
        return f"An error occurred: {str(e)}"


@app.post("/git-diff")
async def generate_parameterized_git_diff(request: GitDiffRequest):
    if request.repo_name not in REPO_CONFIGS:
        raise HTTPException(status_code=400, detail="Invalid repository name")

    repo_path = REPO_CONFIGS[request.repo_name]["path"]
    git_diff = get_parameterized_git_diff(repo_path, request)

    if git_diff.startswith("Error:") or git_diff.startswith("An error occurred:"):
        raise HTTPException(status_code=500, detail=git_diff)

    return {"diff": git_diff}


@app.get("/indexing-status/{repo_name}")
async def get_indexing_status(repo_name: str):
    if repo_name not in REPO_CONFIGS:
        raise HTTPException(status_code=400, detail=f"Invalid repository name: {repo_name}")
    return detailed_indexing_statuses[repo_name]


async def index_repository(websocket: WebSocket, data: dict):
    repo_name = data.get('repo_name')
    if repo_name not in REPO_CONFIGS:
        await websocket.send_json({'error': f"Invalid repository name: {repo_name}"})
        return

    try:
        config = REPO_CONFIGS[repo_name]
        repo_path = config['path']

        # Check if directory exists and is accessible
        if not os.path.isdir(repo_path):
            raise Exception(f"Repository path does not exist or is not a directory: {repo_path}")

        if not os.access(repo_path, os.R_OK):
            raise Exception(f"No read permission for repository path: {repo_path}")

        detailed_indexing_statuses[repo_name] = DetailedIndexingStatus(
            status=IndexingState.IN_PROGRESS,
            message=f"Indexing started for {repo_name}",
            last_updated=time.time(),
            files_to_index=[],
            current_file="",
            processed_files=[],
            total_files=0,
            processed_count=0
        )
        await websocket.send_json({'indexing_status': detailed_indexing_statuses[repo_name].model_dump()})

        # Check if 'code_chunks' collection exists, create if it doesn't
        logger.info(f"Indexing repository: {repo_name}")
        ensure_collection_exists(qclient, "code_chunks", VECTOR_SIZE)

        config = REPO_CONFIGS[repo_name]
        repo_path = config['path']
        parser = config['parser']

        git_repo = GitRepo(repo_path)
        last_indexed_commit = load_last_indexed_commit(repo_path)

        if detect_changes(git_repo, last_indexed_commit):
            allowed_extensions = ['.py'] if config['language'] == 'python' else ['.js', '.jsx', '.ts', '.tsx']
            files_to_index = get_files_to_index(git_repo, last_indexed_commit, allowed_extensions)
            if len(files_to_index) > 100:
                raise ValueError(f"The number of files is too high: {len(files_to_index)}")

            detailed_indexing_statuses[repo_name].files_to_index = [str(f) for f in files_to_index]
            detailed_indexing_statuses[repo_name].total_files = len(files_to_index)
            await websocket.send_json({'indexing_status': detailed_indexing_statuses[repo_name].model_dump()})

            chunks = []
            for file_path in files_to_index:
                try:
                    detailed_indexing_statuses[repo_name].current_file = str(file_path)
                    detailed_indexing_statuses[repo_name].last_updated = time.time()
                    await websocket.send_json({'indexing_status': detailed_indexing_statuses[repo_name].model_dump()})

                    file_chunks = chunk_code_file(file_path, parser)
                    chunks.extend(file_chunks)

                    detailed_indexing_statuses[repo_name].processed_files.append(str(file_path))
                    detailed_indexing_statuses[repo_name].processed_count += 1
                    await websocket.send_json({'indexing_status': detailed_indexing_statuses[repo_name].model_dump()})
                except Exception as e:
                    logger.error(f"Error processing {file_path}: {str(e)}")

            if not chunks:
                detailed_indexing_statuses[repo_name].status = IndexingState.COMPLETED
                detailed_indexing_statuses[repo_name].message = f"No new changes detected for index: {repo_name}"
                detailed_indexing_statuses[repo_name].last_updated = time.time()
                await websocket.send_json({'indexing_status': detailed_indexing_statuses[repo_name].model_dump()})

            processed_chunks = []
            for chunk in chunks:
                if len(str(chunk.content)) > 0:
                    split_chunks = split_code_chunk(chunk)
                    processed_chunks.extend(split_chunks)

            chunk_contents = [chunk.content for chunk in processed_chunks]
            embeddings, _ = get_embeddings(texts=chunk_contents, batch_size=5)

            if embeddings:
                store_chunks_multi(
                    qclient,
                    "code_chunks",
                    processed_chunks[:len(embeddings)],
                    embeddings,
                    repo_name
                )
                save_last_indexed_commit(repo_path, git_repo.get_latest_commit())
                detailed_indexing_statuses[repo_name].status = IndexingState.COMPLETED
                detailed_indexing_statuses[repo_name].message = f"Repository {repo_name} indexed successfully"
                detailed_indexing_statuses[repo_name].last_updated = time.time()
            else:
                detailed_indexing_statuses[repo_name].status = IndexingState.COMPLETED
                detailed_indexing_statuses[repo_name].message = f"No embeddings generated for {repo_name}"
                detailed_indexing_statuses[repo_name].last_updated = time.time()

            await websocket.send_json({'indexing_status': detailed_indexing_statuses[repo_name].model_dump()})
        else:
            detailed_indexing_statuses[repo_name].status = IndexingState.COMPLETED
            detailed_indexing_statuses[repo_name].message = f"No changes detected for {repo_name}"
            detailed_indexing_statuses[repo_name].last_updated = time.time()

        await websocket.send_json({'indexing_status': detailed_indexing_statuses[repo_name].model_dump()})

    except Exception as e:
        error_trace = traceback.format_exc()
        logger.error(f"Error during indexing of {repo_name}: {str(e)}\n{error_trace}")
        detailed_indexing_statuses[repo_name].status = IndexingState.FAILED
        detailed_indexing_statuses[repo_name].message = f"Error indexing {repo_name}: {str(e)}"
        detailed_indexing_statuses[repo_name].last_updated = time.time()
        await websocket.send_json({'indexing_status': detailed_indexing_statuses[repo_name].model_dump()})


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=7779)