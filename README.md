# archivesspace-toolkit
An environment for local development and testing of tools to update UCLA's ArchivesSpace records.

## Local Setup
Build and run the containers using docker-compose.

`docker-compose build`
`docker-compose up -d`

Wait until you see the below message in the `as_aspace` container logs:
```
Welcome to ArchivesSpace!
You can now point your browser to http://localhost:8080
```

The staff interface will be available at `http://localhost:8080`, and the public interface will be at `http://localhost:8081`. Log in with username and password `admin`.

