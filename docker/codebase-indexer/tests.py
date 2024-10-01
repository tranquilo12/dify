from pathlib import Path, WindowsPath

from utils import get_normalized_path, is_ignored, load_gitignore


def print_file_paths(path: str):
    repo_path: Path | WindowsPath = get_normalized_path(path)
    gitignore_spec = load_gitignore(repo_path)
    for file_path in repo_path.rglob("*.py"):
        if not is_ignored(file_path, repo_path, gitignore_spec):
            print(f"{file_path=}")


if __name__ == "__main__":
    path = "/volumes/IntoTheDeep"
    print_file_paths(path)