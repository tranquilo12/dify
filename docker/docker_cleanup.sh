#!/bin/bash

# Function to print section headers
print_header() {
    echo "========================================="
    echo "$1"
    echo "========================================="
}

# Function to prompt for confirmation
confirm() {
    read -p "$1 (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]
    then
        return 0
    else
        return 1
    fi
}

# Stop all running containers
print_header "Stopping all running containers"
if confirm "Do you want to stop all running containers?"; then
    docker stop $(docker ps -aq)
else
    echo "Skipping container stop."
fi

# Remove all containers
print_header "Removing all containers"
if confirm "Do you want to remove all containers (including stopped ones)?"; then
    docker rm $(docker ps -aq)
else
    echo "Skipping container removal."
fi

# Remove all images
print_header "Removing all images"
if confirm "Do you want to remove all Docker images?"; then
    docker rmi $(docker images -q) -f
else
    echo "Skipping image removal."
fi

# Prune containers
print_header "Pruning containers"
if confirm "Do you want to prune containers?"; then
    docker container prune -f
else
    echo "Skipping container prune."
fi

# Prune images
print_header "Pruning images"
if confirm "Do you want to prune images?"; then
    docker image prune -a -f
else
    echo "Skipping image prune."
fi

# Prune volumes
print_header "Pruning volumes"
if confirm "Do you want to prune volumes?"; then
    docker volume prune -f
else
    echo "Skipping volume prune."
fi

# Prune networks
print_header "Pruning networks"
if confirm "Do you want to prune networks?"; then
    docker network prune -f
else
    echo "Skipping network prune."
fi

# System prune
print_header "System prune (removes all unused containers, networks, images, and optionally, volumes)"
if confirm "Do you want to perform a system prune?"; then
    if confirm "Include volumes in system prune? (This will remove all unused data)"; then
        docker system prune -a -f --volumes
    else
        docker system prune -a -f
    fi
else
    echo "Skipping system prune."
fi

print_header "Cleanup complete"
echo "Your Docker system has been cleaned and pruned according to your choices."
