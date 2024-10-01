#!/bin/bash

# Print environment variables (be careful with sensitive information)
echo "Environment variables:"
env | grep -v "_KEY"

# Print the current working directory
echo "Current working directory:"
pwd

# List the contents of the current directory
echo "Contents of the current directory:"
ls -la

# Print the contents of the config file
echo "Contents of config.yaml:"
cat /app/config.yaml

# Execute the LiteLLM command
echo "Executing LiteLLM command:"
echo "litellm --config /app/config.yaml --port 4000"
exec litellm --config /app/config.yaml --port 4000