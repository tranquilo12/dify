import os

import click
import requests

API_URL = "http://localhost:7779"  # Adjust if needed


@click.group()
def rr():
    """Rational-RAG CLI tool for managing repositories."""
    pass


@rr.command()
@click.argument('repo_path', type=click.Path(exists=True))
@click.option('--name', help='Name for the repository (defaults to directory name)')
@click.option(
    '--language', type=click.Choice(['python', 'typescript']), required=True,
    help='Programming language of the repository'
)
def add(repo_path: str, name: str, language: str):
    """Add a repository to the RAG system."""
    repo_path = os.path.abspath(repo_path)
    if not name:
        name = os.path.basename(repo_path)

    response = requests.post(
        f"{API_URL}/repositories", json={
            "action"   : "add",
            "repo_name": name,
            "repo_path": repo_path,
            "language" : language
        }
    )

    if response.status_code == 200:
        click.echo(f"Repository '{name}' added successfully.")
    else:
        click.echo(f"Failed to add repository: {response.text}")


@rr.command()
@click.argument('repo_name')
def remove(repo_name: str):
    """Remove a repository from the RAG system."""
    response = requests.post(
        f"{API_URL}/repositories", json={
            "action"   : "remove",
            "repo_name": repo_name
        }
    )

    if response.status_code == 200:
        click.echo(f"Repository '{repo_name}' removed successfully.")
    else:
        click.echo(f"Failed to remove repository: {response.text}")


@rr.command()
def show():
    """List all repositories in the RAG system."""
    response = requests.get(f"{API_URL}/collections")

    if response.status_code == 200:
        collections = response.json()['collections']
        click.echo("Managed repositories:")
        for repo in collections:
            click.echo(f"- {repo}")
    else:
        click.echo("Failed to fetch repository list.")


if __name__ == '__main__':
    rr()