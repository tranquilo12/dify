import logging
import os
import time

import requests
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

API_URL = "http://localhost:7779"  # Adjust if needed
WATCH_DIRECTORY = "/app/codebase"


class RepoEventHandler(FileSystemEventHandler):
    def __init__(self):
        self.pending_events = set()
        self.last_processed_time = time.time()

    def on_any_event(self, event):
        if event.is_directory:
            return

        repo_path = os.path.dirname(event.src_path)
        repo_name = os.path.basename(repo_path)
        self.pending_events.add(repo_name)

    def process_events(self):
        current_time = time.time()
        if current_time - self.last_processed_time > 5 and self.pending_events:  # 5-second debounce
            for repo_name in self.pending_events:
                self.trigger_reindex(repo_name)
            self.pending_events.clear()
            self.last_processed_time = current_time

    def trigger_reindex(self, repo_name):
        logging.info(f"Changes detected in {repo_name}. Triggering reindex...")
        try:
            response = requests.post(f"{API_URL}/reindex", json={"repo_name": repo_name})
            if response.status_code == 200:
                logging.info(f"Reindexing of {repo_name} initiated successfully.")
            else:
                logging.error(f"Failed to initiate reindexing of {repo_name}: {response.text}")
        except requests.RequestException as e:
            logging.error(f"Error triggering reindex for {repo_name}: {str(e)}")


if __name__ == "__main__":
    event_handler = RepoEventHandler()
    observer = Observer()
    observer.schedule(event_handler, path=WATCH_DIRECTORY, recursive=True)
    observer.start()
    logging.info(f"Started watching for changes in {WATCH_DIRECTORY}")
    try:
        while True:
            time.sleep(1)
            event_handler.process_events()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()