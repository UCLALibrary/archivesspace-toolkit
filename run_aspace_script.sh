#!/usr/bin/env bash
# Run a Python script in the ArchivesSpace Docker container,
# mounting in config files and copying out any log/output files it creates.

# Be strict about errors, unset variables, and pipeline failures.
set -euo pipefail

# Define the Docker Compose file and data directory for use with ASpace scripts.
# Use dirname and readlink to get the absolute path of the current script's directory,
# and then append the docker-compose_scripts.yml filename to it.
COMPOSE_FILE="$(dirname "$(readlink -f "$0")")/docker-compose_scripts.yml"
export ASPACE_DATA_DIR="${ASPACE_DATA_DIR:-$HOME/aspace-data}"
# Create necessary directories for secrets, logs, and output if they don't already exist.
mkdir -p "${ASPACE_DATA_DIR}/secrets" "${ASPACE_DATA_DIR}/logs" "${ASPACE_DATA_DIR}/output"

# Export the current user's UID and GID for use in the Docker container, 
# so that files created by the container have the correct ownership on the host system.
export ASPACE_UID="$(id -u)"
export ASPACE_GID="$(id -g)"

# Check that the user provided at least one argument (the script name). 
# If not, print usage information and exit with an error code.
if [ "$#" -lt 1 ]; then
  echo "Usage: $(basename "$0") <script_name.py> [script args...]"
  exit 1
fi

# Run the specified Python script in the ArchivesSpace Docker container.
set +e
docker compose -f "${COMPOSE_FILE}" run --rm scripts python "$@"
EXIT_CODE=$?
set -e

exit "${EXIT_CODE}"