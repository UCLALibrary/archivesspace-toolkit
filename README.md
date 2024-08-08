# archivesspace-toolkit
An environment for local development and testing of tools to update UCLA's ArchivesSpace records.

## Local Setup
To support both AMD and ARM architectures, we need to build the main ArchivesSpace container image locally. To do this, first clone the ArchivesSpace repo: https://github.com/archivesspace/archivesspace/. Navigate to the main `archivesspace` directory and build the image, tagging it as `archivesspace-local`:

`docker build . -t archivesspace-local`

Then, navigate back to this repo's main directory (`archivesspace-toolkit`) and run the containers with `docker compose`: 

`docker compose up -d`

Wait until you see the below message in the `as_aspace` container logs:
```
Welcome to ArchivesSpace!
You can now point your browser to http://localhost:8080
```

The staff interface will be available at http://localhost:8080, and the public interface will be at http://localhost:8081. Log in with username and password `admin`.

## Loading Data

1. Retrieve the latest production database dump, named `ucla.sql.gz`, from [Box](https://ucla.app.box.com/folder/279154148440) (ask a teammate if you need access).  Move the file to your `archivesspace-toolkit` project directory.
2. Start your local system, if not already up: `docker compose up -d`, and wait for the application to be ready.
3. Run the following to load the data.  Since database storage is persisted via volume on the host, this will use about 2.7 GB of local storage on your computer.  This will take several minutes, depending on computer:
```
gunzip -dc ucla.sql.gz | docker compose exec -T db mysql -D archivesspace -u root -p123456

# This unzips the database dump to STDOUT, pipes it to mysql running on the db service, loading the data
# into the archivesspace database. The mysql user must be root; password 123456 comes from .docker-compose_db.env.
```
4. Ignore the warning: `mysql: [Warning] Using a password on the command line interface can be insecure.`
5. Quick verification of data load: 
```
docker compose exec db mysql -D archivesspace -u as -pas123 -e 'select count(*) from repository;'
+----------+
| count(*) |
+----------+
|        3 |
+----------+
```
6. This process is repeatable: the import drops existing tables (official ones, at least), so all official content is replaced by this import. (Tables you may have created manually will remain, along with their contents.)
7. If you do want to start fresh:
  * Stop the system with `docker compose down`
  * Remove the database storage: `docker volume rm archivesspace-toolkit_db`
  * Start the system as usual

## Rebuilding Solr Index

TBD - all I know for now is this is a long-running process (14 hours so far....)
