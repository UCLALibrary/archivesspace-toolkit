FROM python:3.11-slim-bullseye

# Set correct timezone
RUN ln -sf /usr/share/zoneinfo/America/Los_Angeles /etc/localtime

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

# Create a link to the archivessnake config file in the home directory,
# allowing live editing via the python/app copy, which is mounted in docker-compose.yml.
RUN (cd .. && ln -s app/.archivessnake.yml)

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
