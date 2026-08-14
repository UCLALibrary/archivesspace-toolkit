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

### Begin SSH TUNNEL SETUP ###
# Establish a tunneled database connection, which will run in the background
# until closed by this script. Lyrasis requires database connections be tunneled
# through their bastion server.  The user on that server is associated with
# a specific person, but can be shared in this context.
BASTION_USER="ucla-kohler@aspace-hosting-production-bastion.lyrtech.org"
# This is the PRODUCTION db server; Lyrasis says they don't offer access to the test db.
ASPACE_DB_SERVER="aspace-hosting-production-db-shared-p1.lyrtech.org"
LOCAL_PORT=3306 # Consider randomizing this if problems occur with multiple users.
REMOTE_PORT=3306 # Must always be this.
# Each user must copy this into their home .ssh directory, with proper permissions set:
# chmod 600 ~/.ssh/id_aspace_ssh
IDENTITY_FILE="~/.ssh/id_aspace_ssh"
CONTROL_SOCKET="~/socket_aspace_db"

# Create the tunnel, using a named master socket to allow later commands to easily close it.
# This uses connection sharing features, but probably connections will not be shared in real use.
# -M: "master" mode for connection sharing
# -S: control socket for connection sharing
# -f: ssh connection will run in background
# -N: no remote command, just forward port
# -T: no pseudo-TTY, this is not interactive
# -L: forward connection from local port to remote port on the remote server
echo "Opening SSH tunnel for production database connection..."
ssh -i "${IDENTITY_FILE}" -M -S "${CONTROL_SOCKET}" -fNT \
-L "${LOCAL_PORT}":"${ASPACE_DB_SERVER}":"${REMOTE_PORT}" \
"${BASTION_USER}"
### End SSH TUNNEL SETUP ###

# Disable strict error failure handling (is this really correct?)
set +e

# Run the specified Python script in the ArchivesSpace Docker container.
docker compose -f "${COMPOSE_FILE}" run --rm scripts python "$@"
EXIT_CODE=$?
set -e

# Close the database tunnel.
echo "Closing SSH tunnel for production database connection..."
ssh -S "${CONTROL_SOCKET}" -O exit "${BASTION_USER}"

# Use the docker program's exit code.
exit "${EXIT_CODE}"
