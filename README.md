# archivesspace-toolkit
An environment for local development and testing of tools to update UCLA's ArchivesSpace records.

## Local Setup
To support both AMD and ARM architectures, we need to build the main ArchivesSpace container image locally. To do this, first clone the ArchivesSpace repo: https://github.com/archivesspace/archivesspace/. Navigate to the main `archivesspace` directory and build the image, tagging it as `archivesspace-local`:

`docker build -t archivesspace-local`

Then, navigate back to this repo's main directory (`archivesspace-toolkit`) and run the containers with `docker compose`: 

`docker compose up -d`

Wait until you see the below message in the `as_aspace` container logs:
```
Welcome to ArchivesSpace!
You can now point your browser to http://localhost:8080
```

The staff interface will be available at http://localhost:8080, and the public interface will be at http://localhost:8081. Log in with username and password `admin`.

