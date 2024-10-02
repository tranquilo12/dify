import os
import json
import time
import requests
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from git import Repo
import pathspec

REPO_CONFIGS = json.load(open('repo_configs.json', 'r'))

# Configuration
REPO_PATH = REPO_CONFIGS['dify']['path']  # Update this to your local repository path
INDEXER_URL = "http://localhost:7779"  # Update if your indexer service is on a different host/port
LOG_FILE = "/app/data/indexer.log"  # Update this to the path of your indexer log file
REPO_NAME = os.path.basename(REPO_PATH)  # Assuming the repo name is the same as the directory name


def load_gitignore(repo_path):
    repo = Repo(repo_path)
    gitignore_path = os.path.join(repo.working_tree_dir, '.gitignore')
    if os.path.exists(gitignore_path):
        with open(gitignore_path, 'r') as gitignore_file:
            return pathspec.PathSpec.from_lines('gitwildmatch', gitignore_file)
    return None


gitignore_spec = load_gitignore(REPO_PATH)


class RepoChangeHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        if event.is_directory:
            return

        # Get the relative path
        rel_path = os.path.relpath(event.src_path, REPO_PATH)

        # Check if the file should be ignored
        if gitignore_spec and gitignore_spec.match_file(rel_path):
            print(f"Ignoring change in file (matches .gitignore): {rel_path}")
            return

        print(f"Detected change in file: {rel_path}")
        trigger_reindex()


def trigger_reindex():
    print(f"Triggering re-index for repository: {REPO_NAME}")
    try:
        response = requests.post(f"{INDEXER_URL}/force-index/{REPO_NAME}")
        if response.status_code == 200:
            print("Re-indexing triggered successfully")
        else:
            print(f"Error triggering re-indexing: {response.text}")
    except requests.RequestException as e:
        print(f"Failed to communicate with indexer service: {e}")


class LogHandler(FileSystemEventHandler):
    def on_modified(self, event):
        if event.src_path == LOG_FILE:
            with open(LOG_FILE, 'r') as f:
                f.seek(0, 2)  # Move to the end of the file
                while True:
                    line = f.readline()
                    if not line:
                        break
                    if "Starting periodic indexing" in line or "Force indexing triggered" in line:
                        print("Detected start of indexing process")
                    elif "Finished periodic indexing" in line or "Force indexing completed" in line:
                        print("Detected completion of indexing process")
                    elif "Error during periodic indexing" in line or "Error during force indexing" in line:
                        print("Detected error in indexing process")


def monitor_repo():
    event_handler = RepoChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path=REPO_PATH, recursive=True)
    observer.start()
    return observer


def monitor_logs():
    event_handler = LogHandler()
    observer = Observer()
    observer.schedule(event_handler, path=os.path.dirname(LOG_FILE), recursive=False)
    observer.start()
    return observer


def main():
    print(f"Starting monitoring for repository: {REPO_PATH}")
    print("Make changes to your repository, and this script will detect them and trigger re-indexing.")
    print("Changes to files matching .gitignore patterns will be ignored.")

    repo_observer = monitor_repo()
    log_observer = monitor_logs()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Monitoring stopped by user")
    finally:
        repo_observer.stop()
        log_observer.stop()
        repo_observer.join()
        log_observer.join()


if __name__ == "__main__":
    main()