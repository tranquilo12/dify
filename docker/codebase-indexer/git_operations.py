import json
from pathlib import Path
from typing import List, Optional

from git import GitCommandError, Repo


class GitRepo:
    def __init__(self, repo_path: str):
        self.repo = Repo(repo_path)
        self.path = Path(repo_path)

    def get_latest_commit(self) -> str:
        return self.repo.head.commit.hexsha

    def get_changed_files(self, since_commit: str) -> List[Path]:
        try:
            diff = self.repo.git.diff(since_commit, name_only=True).split('\n')
            return [self.path / file for file in diff if file]
        except GitCommandError:
            return []


def detect_changes(repo: GitRepo, last_indexed_commit: Optional[str]) -> bool:
    if last_indexed_commit is None:
        return True
    return repo.get_latest_commit() != last_indexed_commit


def get_files_to_index(repo: GitRepo, last_indexed_commit: Optional[str], allowed_extensions: List[str]) -> List[Path]:
    if last_indexed_commit is None:
        # Index all files if no previous indexing
        all_files = []
        for file in repo.path.rglob('*'):
            if file.is_file() and any(file.suffix.endswith(ext) for ext in allowed_extensions):
                all_files.append(file)
        return all_files
    else:
        changed_files = repo.get_changed_files(last_indexed_commit)
        return [f for f in changed_files if any(f.suffix.endswith(ext) for ext in allowed_extensions)]


def get_commit_file_path(repo_path: str) -> Path:
    repo_name = Path(repo_path).name
    return Path(f'{repo_name}_last_indexed_commit.json')


def save_last_indexed_commit(repo_path: str, commit_hash: str):
    file_path = get_commit_file_path(repo_path)
    with file_path.open('w') as f:
        json.dump({"last_commit": commit_hash}, f)


def load_last_indexed_commit(repo_path: str) -> Optional[str]:
    file_path = get_commit_file_path(repo_path)
    if file_path.exists():
        with file_path.open('r') as f:
            data = json.load(f)
        return data.get("last_commit")
    return None