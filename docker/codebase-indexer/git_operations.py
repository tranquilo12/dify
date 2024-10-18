import json
from pathlib import Path
from typing import List, Optional

from git import GitCommandError, InvalidGitRepositoryError, Repo


def is_valid_git_repo(path: str) -> bool:
    try:
        Repo(path)
        return True
    except InvalidGitRepositoryError:
        return False


class GitRepo:
    def __init__(self, repo_path: str):
        if not is_valid_git_repo(repo_path):
            raise Exception(f"Invalid Git repository: {repo_path}")
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


def get_commit_file_path(repo_path: str) -> Path:
    repo_name = Path(repo_path).name
    return Path(f'{repo_name}_last_indexed_commit.json')


def save_last_indexed_commit(repo_path: str, commit_hash: str):
    file_path = get_commit_file_path(repo_path)
    try:
        data = {"last_commit": commit_hash}
        with open(str(file_path), 'w', encoding='utf-8') as f:
            json.dump(data, fp=f, ensure_ascii=False, indent=2)  # type: ignore
    except Exception as e:
        print(f"Error saving last indexed commit: {e}")


def load_last_indexed_commit(repo_path: str) -> Optional[str]:
    file_path = get_commit_file_path(repo_path)
    if file_path.exists():
        with file_path.open('r') as f:
            data = json.load(f)
        return data.get("last_commit")
    return None