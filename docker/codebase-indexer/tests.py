from pathlib import Path, WindowsPath

from utils import get_normalized_path, is_ignored, load_gitignore


def print_file_paths(path: str):
    react_file_extensions = ('.js', '.jsx', '.ts', '.tsx')
    # react_file_extensions = ['.py']
    repo_path: Path | WindowsPath = get_normalized_path(path)
    included_files = []
    for ext in react_file_extensions:
        gitignore_spec = load_gitignore(repo_path)
        for file_path in repo_path.rglob(f"*{ext}"):
            filename = str(file_path).split("/")[-1]
            if ("webpack" not in filename) and ("jest" not in filename):
                if not is_ignored(file_path, repo_path, gitignore_spec):
                    included_files.append(file_path)

    print(f"Is included: {len(included_files)}")
    print(f"All files: \n{included_files}")


if __name__ == "__main__":
    # p = "/Users/shriramsunder/Projects/ParationalServices/ParationalAddOn/officeAddOn"
    # p = "/volumes/dify/docker"
    p = "/volumes/erudite"
    print_file_paths(p)