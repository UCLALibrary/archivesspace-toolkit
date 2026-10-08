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

### Begin CONFIG FILE LOOKUP ###
# The config file passed to the Python script decides which ArchivesSpace instance
# (test or production) the script talks to, so it also decides which database
# the tunnel below connects to. Find it among the Python script's arguments.
CONFIG_FILE=""
ARGS=("$@")
for ((i = 0; i < ${#ARGS[@]}; i++)); do
  ARG_NAME="${ARGS[i]%%=*}"
  if [ "${ARG_NAME}" = "--config_file" ]; then
    if [ "${ARGS[i]}" != "${ARG_NAME}" ]; then
      # --config_file=path
      CONFIG_FILE="${ARGS[i]#*=}"
    else
      # --config_file path
      CONFIG_FILE="${ARGS[i + 1]:-}"
    fi
  fi
done

if [ -z "$CONFIG_FILE" ]; then
  echo "ERROR: --config_file argument is required." >&2
  exit 1
fi
### End CONFIG FILE LOOKUP ###

### Begin SSH TUNNEL SETUP ###
# Establish a tunneled database connection, which will run in the background
# until closed by this script. Lyrasis requires database connections be tunneled
# through their bastion server.  The user on that server is associated with
# a specific person, but can be shared in this context.
#
# The environment-specific settings come from the db_tunnel section of the config file:
#   database_server:  Lyrasis database server for this environment
#   bastion_server:   Lyrasis bastion server
#   bastion_user:     User on the bastion server
#   bastion_key_file: SSH private key for that user. Each user must copy this into
#                     their home .ssh directory, with proper permissions set, e.g.:
#                     chmod 600 ~/.ssh/id_aspace_ssh
# The local port must be the MySQL default, since the Python scripts don't set a port
# when connecting. This means only one tunnel can be open on this server at a time.
LOCAL_PORT=3306
REMOTE_PORT=3306 # Must always be this.
TUNNEL_OPEN="false"

# Print the db_tunnel settings from the given config file, one per line.
# This uses the Python YAML library in the Docker image, so the file is read
# in the same way, and from the same (container) path, as the Python scripts read it.
# This will later be used to establish the SSH tunnel.
read_tunnel_settings() {
  docker compose -f "${COMPOSE_FILE}" run --rm -T scripts python -c '
import sys
import yaml

config_file = sys.argv[1]
try:
    with open(config_file) as f:
        tunnel = (yaml.safe_load(f) or {}).get("db_tunnel") or {}
except (OSError, yaml.YAMLError) as error:
    sys.exit(f"ERROR: Cannot read config file {config_file}: {error}")

keys = ["database_server", "bastion_server", "bastion_user", "bastion_key_file"]
missing = ", ".join(key for key in keys if not tunnel.get(key))
if missing:
    sys.exit(f"ERROR: {config_file} is missing db_tunnel setting(s): {missing}")
for key in keys:
    print(tunnel[key])
' "$1"
}

# Close the database tunnel, but only if this run of the script opened it.
close_tunnel() {
  if [ "${TUNNEL_OPEN}" = "true" ]; then
    echo "Closing SSH tunnel for database connection..."
    ssh -S "${CONTROL_SOCKET}" -O exit "${BASTION_DESTINATION}" || true
  fi
}
# Close the tunnel however this script ends: normally, on error, or when interrupted.
trap close_tunnel EXIT

TUNNEL_OUTPUT="$(read_tunnel_settings "${CONFIG_FILE}")"
mapfile -t TUNNEL_SETTINGS <<< "${TUNNEL_OUTPUT}"
if [ "${#TUNNEL_SETTINGS[@]}" -ne 4 ]; then
  echo "ERROR: Could not read db_tunnel settings from ${CONFIG_FILE}." >&2
  exit 1
fi
DATABASE_SERVER="${TUNNEL_SETTINGS[0]}"
BASTION_SERVER="${TUNNEL_SETTINGS[1]}"
BASTION_USER="${TUNNEL_SETTINGS[2]}"
BASTION_KEY_FILE="${TUNNEL_SETTINGS[3]}"
BASTION_DESTINATION="${BASTION_USER}@${BASTION_SERVER}"
# One socket per bastion user, so test and production tunnels can't be confused.
# (ssh expands the ~ itself, here and in the key file path.)
CONTROL_SOCKET="~/socket_aspace_db_${BASTION_USER}"

# Create the tunnel, using a named master socket to allow later commands to easily close it.
# This uses connection sharing features, but probably connections will not be shared in real use.
# -M: "master" mode for connection sharing
# -S: control socket for connection sharing
# -f: ssh connection will run in background
# -N: no remote command, just forward port
# -T: no pseudo-TTY, this is not interactive
# -L: forward connection from local port to remote port on the remote server
# ExitOnForwardFailure: fail if the local port is already in use. Without this, ssh only
# warns, and the Python script would then use whatever tunnel is already on that port,
# which could be another run's tunnel to the *other* environment's database.
echo "Opening SSH tunnel for database connection to ${DATABASE_SERVER} as ${BASTION_USER}..."
if ! ssh -i "${BASTION_KEY_FILE}" -M -S "${CONTROL_SOCKET}" -fNT \
  -o ExitOnForwardFailure=yes \
  -L "${LOCAL_PORT}":"${DATABASE_SERVER}":"${REMOTE_PORT}" \
  "${BASTION_DESTINATION}"; then
  echo "ERROR: Could not open SSH tunnel; see the ssh message above." >&2
  echo "If it says it cannot listen to port ${LOCAL_PORT}, another run of this script" >&2
  echo "has a tunnel open: wait for it to finish, then try again." >&2
  exit 1
fi
TUNNEL_OPEN="true"

### End SSH TUNNEL SETUP ###

# Run the specified Python script in the ArchivesSpace Docker container.
# Capture its exit code rather than stopping on failure, so it can be passed on below.
EXIT_CODE=0
docker compose -f "${COMPOSE_FILE}" run --rm scripts python "$@" || EXIT_CODE=$?

# Use the docker program's exit code. The tunnel is closed by the EXIT trap.
exit "${EXIT_CODE}"
