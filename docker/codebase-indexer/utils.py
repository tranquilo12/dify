import json
import os
from dataclasses import dataclass
from pathlib import Path, WindowsPath
from typing import Dict, List, Optional, Tuple, Union

import numpy as np
import pathspec
import requests
import tiktoken
import tree_sitter_python as tspython
import tree_sitter_typescript as xtypescript
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_fixed
from tqdm import trange
from tree_sitter import Language, Parser, Point

from git_operations import (
    GitRepo, detect_changes, get_files_to_index, load_last_indexed_commit, save_last_indexed_commit,
)
from logger import indexer_logger as logger

TIK_ENCODER = tiktoken.get_encoding('cl100k_base')
load_dotenv()


@dataclass
class CodeChunk:
    """
    Represents a chunk of code with metadata.

    Attributes
    ----------
    content : str
        The actual code content.
    chunk_type : str
        Type of the chunk (e.g., 'function', 'class', 'file').
    start_byte : int
        Starting byte position in the original file.
    end_byte : int
        Ending byte position in the original file.
    start_point : tuple
        Starting (line, column) in the original file.
    end_point : tuple
        Ending (line, column) in the original file.
    file_path : str
        Path to the file containing this chunk.
    """
    content: str
    chunk_type: str
    start_byte: int
    end_byte: int
    start_point: Point
    end_point: Point
    file_path: str


# TODO: Need to change the tokenizer to use the voyage tokenizer for embeddings
def split_code_chunk(chunk: CodeChunk, max_tokens: int = 8192) -> List[CodeChunk]:
    content = chunk.content
    tokens = TIK_ENCODER.encode(
        content, disallowed_special=(
                TIK_ENCODER.special_tokens_set - {'<|endoftext|>', '<|fim_prefix|>', '<|fim_middle|>', '<|fim_suffix|>',
                                                  '<|endofprompt|>'})
    )

    if len(tokens) <= max_tokens:
        return [chunk]

    splits = []
    start = 0
    while start < len(tokens):
        end = start + max_tokens
        if end >= len(tokens):
            end = len(tokens)
        else:
            # Find the last newline within the token limit
            while end > start and tokens[end] != 10:  # 10 is the token for newline
                end -= 1
            if end == start:
                end = start + max_tokens  # If no newline found, just cut at max_tokens

        split_content = TIK_ENCODER.decode(tokens[start:end])
        splits.append(
            CodeChunk(
                content=split_content,
                chunk_type=f"{chunk.chunk_type}_split",
                start_byte=chunk.start_byte + start,
                end_byte=chunk.start_byte + end,
                start_point=chunk.start_point,
                end_point=chunk.end_point,
                file_path=chunk.file_path
            )
        )
        start = end

    return splits


def setup_tree_sitter_py() -> Parser:
    """
    Set up the tree-sitter parser for Python.

    Returns
    -------
    Parser
        Configured tree-sitter parser for Python.
    """
    py_lang = Language(tspython.language())
    parser = Parser(language=py_lang)
    return parser


def setup_tree_sitter_ts() -> Parser:
    """
    Set up the tree-sitter parser for Python.

    Returns
    -------
    Parser
        Configured tree-sitter parser for Python.
    """
    lang = Language(xtypescript.language_tsx())
    parser = Parser(language=lang)
    return parser


def initialize_qdrant() -> QdrantClient:
    """
    Initialize an in-memory Qdrant client and create a collection.

    Returns
    -------
    QdrantClient
        Configured Qdrant client with an 'IntoTheDeep' collection.
    """
    qdrant_api_key = os.environ.get("QDRANT_API_KEY")
    qdrant_url = os.environ.get("QDRANT_URL")
    client = QdrantClient(qdrant_url, api_key=qdrant_api_key)
    return client


def get_normalized_path(path: Union[str, Path]) -> Path:
    if isinstance(path, str):
        path = Path(path)
    if os.name == 'nt':
        return WindowsPath(path).resolve()
    else:
        return Path(path).resolve()


def load_gitignore(repo_path: Path) -> Optional[pathspec.PathSpec]:
    for directory in [repo_path, repo_path.parent, repo_path.parent.parent]:
        gitignore_path = directory / ".gitignore"
        if gitignore_path.exists():
            with gitignore_path.open("r") as gitignore_file:
                return pathspec.PathSpec.from_lines("gitwildmatch", gitignore_file)
    return None


def is_ignored(path: Path, repo_root: Path, gitignore_spec: Optional[pathspec.PathSpec]) -> bool:
    # Normalize paths
    path = get_normalized_path(path)
    repo_root = get_normalized_path(repo_root)

    # Make path relative to repo root
    try:
        relative_path = path.relative_to(repo_root).as_posix()
    except ValueError:
        # If path is not relative to repo_root, consider it as not ignored
        return False

    if gitignore_spec:
        # Use case-insensitive matching on Windows
        match_func = gitignore_spec.match_file if os.name != 'nt' else lambda p: gitignore_spec.match_file(p.lower())
        if match_func(relative_path):
            return True

    # Additional check for node_modules
    if "node_modules" in relative_path.split('/'):
        return True

    return False


def chunk_code_file(file_path: Path, parser: Parser) -> List[CodeChunk]:
    """
    Chunk a Python file into CodeChunk objects.

    Parameters
    ----------
    file_path : str
        Path to the Python file.
    parser : Parser
        Configured tree-sitter parser.

    Returns
    -------
    List[CodeChunk]
        List of CodeChunk objects representing the file content.
    """
    content = file_path.read_bytes()
    # with open(file_path, "rb") as file:
    #     content = file.read()

    try:
        decoded_content = content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            decoded_content = content.decode("latin-1")
        except UnicodeDecodeError:
            print(f"Unable to decode {file_path}. Skipping this file.")
            return []

    tree = parser.parse(bytes(decoded_content, "utf-8"))
    chunks = [CodeChunk(
        decoded_content,
        "file",
        0,
        len(content),
        tree.root_node.start_point,
        tree.root_node.end_point,
        str(file_path),
    )]

    # File-level chunk
    # Function-level and class-level chunks
    for node in tree.root_node.children:
        if node.type in ["function_definition", "class_definition"]:
            chunk_content = decoded_content[node.start_byte: node.end_byte]
            chunks.append(
                CodeChunk(
                    chunk_content,
                    "function" if node.type == "function_definition" else "class",
                    node.start_byte,
                    node.end_byte,
                    node.start_point,
                    node.end_point,
                    str(file_path),
                )
            )

    return chunks


def process_repository_py(repo_path: Path, parser: Parser) -> List[CodeChunk]:
    repo_path = get_normalized_path(repo_path)
    gitignore_spec = load_gitignore(repo_path)
    all_chunks = []

    for file_path in repo_path.rglob("*.py"):
        if not is_ignored(file_path, repo_path, gitignore_spec):
            try:
                chunks = chunk_code_file(file_path, parser)
                all_chunks.extend(chunks)
            except Exception as e:
                print(f"Error processing {str(file_path)}: {str(e)}")

    return all_chunks


def process_repository_ts(repo_path: Path, parser: Parser) -> List[CodeChunk]:
    repo_path = get_normalized_path(repo_path)
    gitignore_spec = load_gitignore(repo_path)
    all_chunks = []

    react_file_extensions = ('.js', '.jsx', '.ts', '.tsx')

    for ext in react_file_extensions:
        for file_path in repo_path.rglob(f"*{ext}"):
            filename = str(file_path).split("/")[-1]
            if ("webpack" not in filename) and ("jest" not in filename):
                if not is_ignored(file_path, repo_path, gitignore_spec):
                    try:
                        chunks = chunk_code_file(str(file_path), parser)
                        all_chunks.extend(chunks)
                    except Exception as e:
                        print(f"Error processing {file_path}: {str(e)}")

    return all_chunks


async def process_repositories(
    repo_configs: Dict[str, Dict[str, Union[str, Parser]]],
    force: bool = False
) -> Dict[str, List[CodeChunk]]:
    all_chunks = {}
    for collection_name, config in repo_configs.items():
        try:
            repo_path = config['path']
            language = config['language']
            parser = config['parser']

            git_repo = GitRepo(repo_path)
            last_indexed_commit = None if force else load_last_indexed_commit(repo_path)

            if force or detect_changes(git_repo, last_indexed_commit):
                logger.info(f"Processing repository: {collection_name}")
                allowed_extensions = ['.py'] if language == 'python' else ['.js', '.jsx', '.ts', '.tsx']
                files_to_index = get_files_to_index(git_repo, last_indexed_commit, allowed_extensions)

                chunks = []
                for file_path in files_to_index:
                    try:
                        file_chunks = chunk_code_file(file_path, parser)
                        chunks.extend(file_chunks)
                    except Exception as e:
                        logger.error(f"Error processing {file_path}: {str(e)}")

                all_chunks[collection_name] = chunks

                save_last_indexed_commit(repo_path, git_repo.get_latest_commit())
                logger.info(f"Finished processing repository: {collection_name}")
            else:
                logger.info(f"No changes detected for {collection_name}")
        except Exception as e:
            logger.error(f"Error processing repository {collection_name}: {str(e)}")

    return all_chunks


@retry(
    wait=wait_fixed(40),  # Wait 40s second between retries
    stop=stop_after_attempt(5),  # Stop after 5 attempts
    retry=retry_if_exception_type((requests.RequestException, Exception)),
    reraise=True
)
def make_embedding_request(payload: str) -> List[np.ndarray]:
    host = os.environ.get("VOYAGE_URL")
    response = requests.post(
        url=f"{host}/embeddings",
        headers={
            "Content-Type" : "application/json",
            "Authorization": f"Bearer {os.getenv('VOYAGE_API_KEY')}"
        },
        data=payload
    )
    response.raise_for_status()
    batch_embeddings = response.json()["data"]
    return [np.array(emb["embedding"]) for emb in batch_embeddings]


def get_embeddings(
    texts: Union[str, List[str]],
    batch_size: int = 32,
) -> Tuple[List[np.ndarray], List[List[str]]]:
    if isinstance(texts, str):
        texts = [texts]

    texts = [text for text in texts if text.strip()]
    if not texts:
        print("Warning: No non-empty texts to embed")
        return [], []

    embeddings = []
    unprocessed_batches = []
    for i in trange(0, len(texts), batch_size, desc="Batches..."):
        batch = texts[i: i + batch_size]
        if isinstance(batch, str):
            batch = [batch]

        payload = json.dumps(
            {
                "model": "voyage-3",  # TODO: Parameterize this
                "input": batch,
            }
        )

        try:
            batch_embeddings = make_embedding_request(payload)
            embeddings.extend(batch_embeddings)
        except Exception as e:
            print(f"Failed to get embeddings for batch starting at index {i}: {str(e)}")
            unprocessed_batches.append(batch)

    return embeddings, unprocessed_batches


def store_chunks_multi(
    client: QdrantClient,
    collection_name: str,
    chunks: List[CodeChunk],
    embeddings: List[np.ndarray],
):
    """
    Store CodeChunks and their embeddings in Qdrant.

    Parameters
    ----------
    client : QdrantClient
        Initialized Qdrant client.
    collection_name: str
        Name of the collection to store the chunks.
    chunks : List[CodeChunk]
        List of CodeChunk objects to store.
    embeddings : List[np.ndarray]
        List of embedding vectors corresponding to the chunks.
    """

    # Prepare points for batch insertion
    points = [
        models.PointStruct(
            id=i,
            vector={'custom_vector': embedding.tolist()},
            payload={
                "content"    : chunk.content,
                "chunk_type" : chunk.chunk_type,
                "file_path"  : chunk.file_path,
                "start_byte" : chunk.start_byte,
                "end_byte"   : chunk.end_byte,
                "start_point": chunk.start_point,
                "end_point"  : chunk.end_point,
            },
        )
        for i, (chunk, embedding) in enumerate(zip(chunks, embeddings))
    ]

    # Batch insert points
    batch_size = 100  # Adjust based on your needs and Qdrants capabilities
    for i in trange(0, len(points), batch_size, desc="To qdrant..."):
        batch = points[i: i + batch_size]
        client.upsert(collection_name=collection_name, points=batch)