#!/usr/bin/env bash
# Run a Python script in the ArchivesSpace Docker container,
# mounting in config files and copying out any log/output files it creates.

# Be strict about errors, unset variables, and pipeline failures.
set -euo pipefail

# Define the Docker Compose file and data directory for use with ASpace scripts.
# Create necessary directories for secrets and logs.
COMPOSE_FILE="docker-compose_scripts.yml"
export ASPACE_DATA_DIR="${ASPACE_DATA_DIR:-$HOME/aspace-data}"
mkdir -p "${ASPACE_DATA_DIR}/secrets" "${ASPACE_DATA_DIR}/logs"

# Check that the user provided at least one argument (the script name). 
# If not, print usage information and exit with an error code.
if [ "$#" -lt 1 ]; then
  echo "Usage: $(basename "$0") <script_name.py> [script args...]"
  exit 1
fi

docker compose -f "${COMPOSE_FILE}" up -d

# Create a temporary marker file in the container so we have a timestamp to compare against
# when looking for newly created files.
MARKER="/tmp/run_marker_$$"
# Use 
docker compose -f "${COMPOSE_FILE}" exec -T scripts touch "${MARKER}"

# Run the specified Python script inside the container, passing along any additional arguments. 
# Capture the exit code for later use.
docker compose -f "${COMPOSE_FILE}" exec scripts python "$@"
EXIT_CODE=$?

# Find anything .json or .log created since the marker was touched, anywhere
# under the working directory, excluding the two paths that are already
# directly mounted to the host (no need to copy those back out).
NEW_FILES=$(docker compose -f "${COMPOSE_FILE}" exec -T scripts bash -c \
  "find /home/aspace/app -type f -newer '${MARKER}' \
    \( -name '*.json' -o -name '*.log' \) \
    -not -path '*/logs/*' -not -path '*/secrets/*'")

# Copy any newly created files back out to the host's data directory, 
# preserving their relative paths.
while IFS= read -r f; do
  [ -n "$f" ] && docker compose -f "${COMPOSE_FILE}" cp "scripts:${f}" "${ASPACE_DATA_DIR}/"
done <<< "${NEW_FILES}"

# Clean up the temporary marker file in the container.
docker compose -f "${COMPOSE_FILE}" exec -T scripts rm -f "${MARKER}"

exit "${EXIT_CODE}"