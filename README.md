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

### Resetting Admin Password

After a database refresh from production, the initial local `admin/admin` account/password will no longer work.

To set the admin password to be the same as the production password for your own user, run this after changing `YOUR_ASPACE_USERNAME` to the appropriate value:
```
docker compose exec db mysql -D archivesspace -u as -pas123 \
-e 'update auth_db as t1, (select * from auth_db where username = "YOUR_ASPACE_USERNAME") as t2 set t1.pwhash = t2.pwhash where t1.username = "admin";'
```

## Rebuilding Solr Index

TBD - all I know for now is this is a long-running process (14 hours so far....)

## Using APIs

All API access is handled by the main application service, `archivesspace`, on port 8089.  This can be reached from the python container.
`curl` example, for now:
```
# Open bash session on python container
docker compose run python bash

# Authenticate
curl -s -F password="admin" "http://archivesspace:8089/users/admin/login"

# Use the session key for all other API requests
curl -H "X-ArchivesSpace-Session: your_session_key" "http://archivesspace:8089/repositories"
```

## Connecting to remote instances (UCLA-specific)

It's possible to access data "live" in the hosted test instance, from the development environment. This requires extra
setup, because the APIs are IP-restricted and must be accessed via HTTPS/TLS.  General notes are in our [internal documentation](https://uclalibrary.atlassian.net/wiki/x/1gOWGg).  For this specific application:

1. Add `127.0.0.1       uclalsc-test.lyrasistechnology.org` to your local `/etc/hosts`
2. Create a tunnel from local machine through our jump server. Local port is arbitrary, I've used `9000`:

   `ssh -NT -L 0.0.0.0:9000:uclalsc-test.lyrasistechnology.org:443 jump`
3. Connect from local machine, or from within Docker, using `https://uclalsc-test.lyrasistechnology.org:9000/api` and appropriate credentials.

## API configuration files

There is a default ArchivesSnake configuration file in `python/.archivessnake.yml`.  This supports access from the running `python` container to the running `archivesspace` container, using default (and non-secret) credentials.

For other configurations, copy `python/.archivessnake.yml` to `python/.archivessnake_secret_DEV.yml` or `python/.archivessnake_secret_TEST.yml`, and edit the `baseurl`, `username`, and `password` fields as appropriate.  These files must be in the `python` directory to be available within the container.

These are excluded from the repository, so contact a teammate if you need specific credentials.
