import json
import logging
import os
import time

import pathspec
import requests
from git import Repo
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

REPO_CONFIGS = json.load(open('repo_configs.json', 'r'))

# Configuration
REPO_PATH = REPO_CONFIGS['dify']['path']  # Update this to your local repository path
INDEXER_URL = "http://localhost:7779"  # Update if your indexer service is on a different host/port
REPO_NAME = os.path.basename(REPO_PATH)  # Assuming the repo name is the same as the directory name


def load_gitignore(repo_path):
    repo = Repo(repo_path)
    gitignore_path = os.path.join(repo.working_tree_dir, '.gitignore')
    if os.path.exists(gitignore_path):
        with open(gitignore_path, 'r') as gitignore_file:
            return pathspec.PathSpec.from_lines('gitwildmatch', gitignore_file)
    return None


gitignore_spec = load_gitignore(REPO_PATH)


def trigger_indexing():
    logger.info(f"Triggering indexing for repository: {REPO_NAME}")
    try:
        response = requests.post(f"{INDEXER_URL}/index/{REPO_NAME}")
        if response.status_code == 200:
            logger.info("Indexing triggered successfully")
            return response.json()
        else:
            logger.error(f"Error triggering indexing: {response.text}")
    except requests.RequestException as e:
        logger.error(f"Failed to communicate with indexer service: {e}")
    return None


def check_indexing_status():
    try:
        response = requests.get(f"{INDEXER_URL}/indexing-status/{REPO_NAME}")
        if response.status_code == 200:
            status = response.json()
            logger.info(f"Indexing status: {status['status']} - {status['message']}")
            return status
        else:
            logger.error(f"Error checking indexing status: {response.text}")
    except requests.RequestException as e:
        logger.error(f"Failed to communicate with indexer service: {e}")
    return None


def simulate_file_change():
    test_file_path = os.path.join(REPO_PATH, 'test_file.txt')
    with open(test_file_path, 'w') as f:
        f.write(f"Test content {time.time()}")
    logger.info(f"Created test file: {test_file_path}")
    return test_file_path


def test_search(query="test"):
    try:
        response = requests.post(f"{INDEXER_URL}/search", json={"text": query, "collection_name": REPO_NAME})
        if response.status_code == 200:
            results = response.json()
            logger.info(f"Search results for query '{query}': {json.dumps(results, indent=2)}")
            return results
        else:
            logger.error(f"Error in search: {response.text}")
    except requests.RequestException as e:
        logger.error(f"Failed to communicate with indexer service: {e}")
    return None


class RepoChangeHandler(FileSystemEventHandler):
    def on_any_event(self, event):
        if event.is_directory:
            return

        rel_path = os.path.relpath(event.src_path, REPO_PATH)

        if gitignore_spec and gitignore_spec.match_file(rel_path):
            logger.info(f"Ignoring change in file (matches .gitignore): {rel_path}")
            return

        logger.info(f"Detected change in file: {rel_path}")
        trigger_indexing()


def monitor_repo():
    event_handler = RepoChangeHandler()
    observer = Observer()
    observer.schedule(event_handler, path=REPO_PATH, recursive=True)
    observer.start()
    return observer


def main():
    logger.info(f"Starting monitoring for repository: {REPO_PATH}")
    logger.info("Make changes to your repository, and this script will detect them and trigger indexing.")
    logger.info("Changes to files matching .gitignore patterns will be ignored.")

    repo_observer = monitor_repo()

    try:
        # Initial indexing
        logger.info("Triggering initial indexing...")
        initial_status = trigger_indexing()
        if initial_status:
            while True:
                status = check_indexing_status()
                if status['status'] == 'completed':
                    break
                time.sleep(2)

        # Test search after initial indexing
        test_search()

        # Simulate file change and re-indexing
        logger.info("Simulating file change...")
        test_file = simulate_file_change()

        # Wait for the file system event to be detected
        time.sleep(2)

        # Check indexing status after file change
        while True:
            status = check_indexing_status()
            if status['status'] == 'completed':
                break
            time.sleep(5)

        # Test search after re-indexing
        test_search()

        # Clean up test file
        os.remove(test_file)
        logger.info(f"Removed test file: {test_file}")

        # Continue monitoring
        logger.info("Continuing to monitor for changes...")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Monitoring stopped by user")
    except Exception as e:
        logger.error(f"An error occurred: {str(e)}")
    finally:
        repo_observer.stop()
        repo_observer.join()


if __name__ == "__main__":
    main()