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

# Move the .archivessnake.yml file to the home directory
RUN mv .archivessnake.yml /home/aspace

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt
