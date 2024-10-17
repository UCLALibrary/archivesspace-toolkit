FROM python:3.12-slim-bookworm

# Set correct timezone
RUN ln -sf /usr/share/zoneinfo/America/Los_Angeles /etc/localtime

# Install git, required to install the alma_api_client package from github.
# Add curl as well for testing connectivity.
RUN apt-get update && apt-get install -y git curl

# Create generic user and group
RUN useradd -c "generic app user" -d /home/aspace -s /bin/bash -m aspace

# Swtich to application directory, create it if it doesn't exist
WORKDIR /home/aspace/app

# Make sure new aspace user owns the directory
RUN chown -R aspace:aspace /home/aspace/app

# Switch to the new user
USER aspace

# Copy the rest of the application code to the working directory with aspace as the owner
COPY --chown=aspace:aspace . .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
