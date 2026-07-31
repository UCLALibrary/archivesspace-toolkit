#!/usr/bin/env bash
# Run a Python script in the ArchivesSpace Docker container,
# mounting in config files and copying out any log/output files it creates.

# Be strict about errors, unset variables, and pipeline failures.
set -euo pipefail

IMAGE="uclalibrary/archivesspace-toolkit:latest"
SECRETS_DIR="/home/ztucker/aspace-config"
LOGS_DIR="/home/ztucker/aspace-output"
mkdir -p "${LOGS_DIR}"

# Check that the user provided at least one argument (the script name). 
#If not, print usage information and exit with an error code.
if [ "$#" -lt 1 ]; then
  echo "Usage: $(basename "$0") <script_name.py> [script args...]"
  exit 1
fi

# Build an array of Docker mount arguments for each config file in the secrets directory.
# Each config file will be mounted into the container at /home/aspace/app/<filename>.
MOUNT_ARGS=()
# Temporarily enable dotglob so we can find config files starting with a dot
shopt -s dotglob
for f in "${SECRETS_DIR}"/*.yml; do
  fname="$(basename "$f")"
  MOUNT_ARGS+=(-v "${f}:/home/aspace/app/${fname}")
done
shopt -u dotglob

CONTAINER_NAME="aspace-run-$$"

# Run without --rm: we need the stopped container around briefly
# so we can pull out whatever log/output files it created, since we
# don't know their exact names in advance.
docker run --name "${CONTAINER_NAME}" "${MOUNT_ARGS[@]}" "${IMAGE}" python "$@"
EXIT_CODE=$?

# Copy out anything newly created (.log, .json) without needing to know
# the exact filename, then discard the container.
for f in $(docker diff "${CONTAINER_NAME}" | awk '$1 == "A" && ($2 ~ /\.log$/ || $2 ~ /\.json$/) {print $2}'); do
  docker cp "${CONTAINER_NAME}:${f}" "${LOGS_DIR}/"
done
docker rm "${CONTAINER_NAME}" >/dev/null

exit "${EXIT_CODE}"