import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

import numpy as np
import pathspec
import requests
import tree_sitter_python as tspython
import tree_sitter_typescript as xtypescript
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from tree_sitter import Language, Parser, Point

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


def initialize_qdrant(collection_names: List[str]) -> QdrantClient:
    """
    Initialize an in-memory Qdrant client and create a collection.

    Returns
    -------
    QdrantClient
        Configured Qdrant client with an 'IntoTheDeep' collection.
    """
    client = QdrantClient(url="http://localhost:6333")
    for collection_name in collection_names:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=models.VectorParams(size=1536, distance=models.Distance.COSINE),
        )
    return client


def load_gitignore(repo_path: str) -> Optional[pathspec.PathSpec]:
    gitignore_path = os.path.join(repo_path, ".gitignore")
    if os.path.exists(gitignore_path):
        with open(gitignore_path, "r") as gitignore_file:
            return pathspec.PathSpec.from_lines("gitwildmatch", gitignore_file)
    return None


def is_ignored(path: str, gitignore_spec: Optional[pathspec.PathSpec]) -> bool:
    if gitignore_spec and gitignore_spec.match_file(path):
        return True
    if "node_modules" in path.split(os.path.sep):
        return True
    return False


def chunk_code_file(file_path: str, parser: Parser) -> List[CodeChunk]:
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
    with open(file_path, "rb") as file:
        content = file.read()

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
        file_path,
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
                    file_path,
                )
            )

    return chunks


def process_repository_py(
    repo_path: str, parser: Parser
) -> List[CodeChunk]:
    gitignore_spec = load_gitignore(repo_path)
    all_chunks = []

    # Prepare the file list
    files = [
        (root, file)
        for root, _, files in os.walk(repo_path)
        for file in files
        if file.endswith(".py")
    ]

    for root, file in files:
        file_path = os.path.join(root, file)
        if not is_ignored(file_path, gitignore_spec):
            try:
                chunks = chunk_code_file(file_path, parser)
                all_chunks.extend(chunks)
            except Exception as e:
                print(f"Error processing {file_path}: {str(e)}")
                print(f"Error details: {type(e).__name__}")

    return all_chunks


def process_repository_ts(
    repo_path: str, parser: Parser
):
    gitignore_spec = load_gitignore(repo_path)
    all_chunks = []

    react_file_extensions = ('.js', '.jsx', '.ts', '.tsx')

    for root, _, files in os.walk(repo_path):
        for file in files:
            if file.endswith(react_file_extensions):
                file_path = os.path.join(root, file)
                if not is_ignored(file_path, gitignore_spec):
                    try:
                        chunks = chunk_code_file(file_path, parser)
                        all_chunks.extend(chunks)
                    except Exception as e:
                        print(f"Error processing {file_path}: {str(e)}")
                        print(f"Error details: {type(e).__name__}")

    return all_chunks


# Update the process_repositories function
def process_repositories(
    repo_configs: Dict[str, Dict[str, Union[str, Parser]]]
) -> Dict[str, List[CodeChunk]]:
    """
    Process multiple repositories and return chunks for each.

    Parameters
    ----------
    repo_configs : Dict[str, Dict[str, Union[str, Parser]]]
        Dictionary mapping collection names to repository configurations.
        Each configuration should have 'path', 'language', and 'parser' keys.

    Returns
    -------
    Dict[str, List[CodeChunk]]
        Dictionary mapping collection names to lists of CodeChunks.
    """
    all_chunks = {}
    for collection_name, config in repo_configs.items():
        repo_path = config['path']
        language = config['language']
        parser = config['parser']

        if language == 'python':
            chunks = process_repository_py(repo_path, parser)
        elif language == 'typescript':
            chunks = process_repository_ts(repo_path, parser)
        else:
            raise ValueError(f"Unsupported language: {language}")

        all_chunks[collection_name] = chunks
    return all_chunks


def get_embeddings(
    texts: Union[str, List[str]],
    batch_size: int = 32,
) -> Union[np.ndarray, List[np.ndarray]]:
    if isinstance(texts, str):
        texts = [texts]

    texts = [text for text in texts if text.strip()]
    if not texts:
        print("Warning: No non-empty texts to embed")
        return []

    embeddings = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i: i + batch_size]
        if isinstance(batch, str):
            batch = [batch]

        # Ensure proper JSON encoding of the input
        payload = json.dumps(
            {
                "model": "voyage-code-2",
                "input": batch
            }
        )
        try:
            response = requests.post(
                url="https://api.voyageai.com/v1/embeddings",
                headers={
                    "Content-Type" : "application/json",
                    "Authorization": f"Bearer {os.environ.get('VOYAGE_API_KEY')}"
                },
                data=payload
            )
            response.raise_for_status()
        except requests.RequestException as e:
            print(f"Error connecting to embedding service: {e}")
            raise

        if response.status_code == 200:
            batch_embeddings = response.json()["data"]
            embeddings.extend([np.array(emb["embedding"]) for emb in batch_embeddings])
        else:
            raise Exception(f"Error in getting embeddings: {response.text}")

    return embeddings


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
    progress : Optional[Progress]
        Rich Progress instance for tracking progress.
    """

    # Prepare points for batch insertion
    points = [
        models.PointStruct(
            id=i,
            vector=embedding.tolist(),
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
    for i in range(0, len(points), batch_size):
        batch = points[i: i + batch_size]
        client.upsert(collection_name=collection_name, points=batch)