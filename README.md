# archivesspace-toolkit
An environment for local development and testing of tools to update UCLA's ArchivesSpace records.

## Local Setup
To support both AMD and ARM architectures, we need to build the main ArchivesSpace container image locally. **Do this only when decided by team, so all developers have the same version.**

To do this, first clone the ArchivesSpace repo: https://github.com/archivesspace/archivesspace/. Navigate to the main `archivesspace` directory and build the image, tagging it as `archivesspace-local`:

`docker build . -t archivesspace-local`

Then, navigate back to this repo's main directory (`archivesspace-toolkit`).

### Dev container

This project comes with a basic dev container definition, in `.devcontainer/devcontainer.json`. It's known to work with VS Code,
and may work with other IDEs like PyCharm.  For VS Code, it also installs the Python, Black (formatter), and Flake8 (linter)
extensions.

The project's directory is available within the container at `/home/aspace/app`.

### Rebuilding the dev container

VS Code builds its own container from the base image. This container may not always get rebuilt when the base image is rebuilt
(e.g., if packages are changed via `requirements.txt`).

If needed, rebuild the dev container by:
1. Close VS Code and wait several seconds for the dev container to shut down (check via `docker ps`).
2. Delete the dev container.
   1. `docker images | grep vsc-archivesspace-toolkit` # vsc-archivesspace-toolkit-LONG_HEX_STRING-uid
   2. `docker image rm -f vsc-archivesspace-toolkit-LONG_HEX_STRING-uid`
3. Start VS Code as usual.

The system takes 30-60 seconds to start up.  The database should be available quickly, but ArchivesSpace is not fully up until
you see the below message in the `as_aspace` container logs:
```
Welcome to ArchivesSpace!
You can now point your browser to http://localhost:8080
```

The staff interface will be available at http://localhost:8080, and the public interface will be at http://localhost:8081. Log in with username and password `admin`,
or your own credentials if using a copy of the production database (see Loading data, below).

## Running code

Running code from a VS Code terminal within the dev container should just work, e.g.: `python some_script.py` (whatever the specific program is).

Otherwise, run a program via docker compose.  From the project directory:

```
# Start the system
$ docker compose up -d

# Open a shell in the container
$ docker compose exec python bash

# Open a Python shell in the container
$ docker compose exec python python
```

## Running tests

Several scripts have tests.  To run tests:
```
$ docker compose exec python python -m unittest
```

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

## API configuration files

There is a default ArchivesSnake configuration file in `python/.archivessnake.yml`.  This supports access from the running `python` container to the running `archivesspace` container, using default (and non-secret) credentials. One secret value in this file is omitted from the repository: the `alma_api_key` for the Alma API. This is required to run the barcoding script, so contact a teammate if you need this value.

For other configurations, copy `python/.archivessnake.yml` to `python/.archivessnake_secret_DEV.yml` or `python/.archivessnake_secret_TEST.yml`, and edit the `baseurl`, `username`, `password`, `database.password` and `alma_config.alma_api_key` fields as appropriate.  These files must be in the `python` directory to be available within the container.

These are excluded from the repository, so contact a teammate if you need specific credentials.

## Updating Barcodes
### General process

Adding barcodes from Alma to ArchivesSpace requires collaboration between DIIT and the Library Special Collections team. Links to the tables mentioned below are availabe in this confluence page: https://uclalibrary.atlassian.net/wiki/x/QQCnIw

The process is as follows:
1. LSC identifies collections to be barcoded and updates the "Airtable list of archival collections" table with Alma and ArchivesSpace identifiers.
2. For each collection, DIIT tests the barcoding script using a local copy of the ArchivesSpace database, or using the test environment without updating ArchivesSpace (i.e. using the `--dry_run` flag). This is to determine which configuration profile to use, if a new profile is needed, or if the data needs to be cleaned up before running the script in the test environment.
3. After the script runs successfully in the local environment, DIIT runs the script in the test environment to update the ArchivesSpace records.
4. If the script matches more than 80% of the items from each source, DIIT runs the script in the production environment.
5. If any items are unmatched in the production environment, or if the test environment did not meet the 80% threshold, DIIT creates a Jira ticket in the LSC project for future cleanup.
6. DIIT fills out the "Completed barcode transfer form" (linked in the confluence page), which updates the "Airtable list of status of the collections run" table. This is done even if the script did not run in the production environment.

### Using the barcoding script
To add barcodes from Alma to ArchivesSpace, use the script `python/add_alma_barcodes_to_archivesspace.py`. 

The script takes the following required arguments:
- `--bib_id`: The Alma bib ID for the collection
- `--holdings_id`: The Alma holdings ID for the collection
- `--resource_id`: The ArchivesSpace resource ID for the collection
- `--profile`: The configuration profile to use to match Alma items to ArchivesSpace top containers. Three profiles are currently available: `indicator_only_matching.py`, `indicator_type_matching.py`, and `series_description_matching.py`.
- `--config_file`: A YAML file containing configuration information used by the script, as described in the "API configuration files" section above.

The script also takes the following optional arguments:
- `--use_db`: If set, the script will get ArchivesSpace top container information from the database instead of the API. This is useful when the API times out.
- `--dry_run`: If set, the script will not make any changes to ArchivesSpace.
- `--print_output`: If set, the script will print the output to the console in addition to writing it to the log file.
- `--use_cache`: If set, the script will use cached Alma and ArchivesSpace data instead of making API calls. This is useful for speeding up the script when testing.

### Example usage

With all containers running, run the script from the main project directory:
```bash
docker compose exec python python add_alma_barcodes_to_archivesspace.py \
    --bib_id 123456789 \
    --holdings_id 987654321 \
    --resource_id 1234 \
    --profile indicator_type_matching.py \
    --config_file .archivessnake_secret_DEV.yml \
    --dry_run \
    --use_cache \
    --print_output
``` 

### Configuration Profiles
Each profile uses a different method to match Alma items to ArchivesSpace top containers. In all cases, the script uses the description field in Alma and the indicator field in ArchivesSpace to generate "keys" that are matched between the two systems.

The profiles are as follows:
- `indicator_type_matching.py`: Matches ArchivesSpace indicator and type to the description field in Alma.
  - Alma descriptions that work with this profile are formatted like "box.001", "folder.002", etc. The script parses the description into a "type" and "indicator" (before and after the period, respectively) and matches these to the corresponding fields of the ArchivesSpace top container.
  - Normalization is applied to the Alma indicator to remove leading zeroes and trailing " RESTRICTED" text.
  - e.g. Alma description "box.001 RESTRICTED" will match ASpace top container with indicator "1" and type "box".
- `indicator_only_matching.py`: Matches ArchivesSpace indicator to the second part of the description field in Alma.
  - As with indicator_type_matching.py, Alma descriptions that work with this profile are formatted like "box.001". The script matches the second part of the description to the ArchivesSpace top container indicator.
  - Normalization is applied to the Alma indicator to remove leading zeroes and trailing " RESTRICTED" text.
  - e.g. Alma description "box.001 RESTRICTED" will match ASpace top container with indicator "1", regardless of type.
- `series_description_matching.py`: Parses ArchivesSpace indicator into "series" and "indicator", and matches both to the description field in Alma.
  - Alma descriptions that work with this profile are formatted like "ser.P box.0011". The script parses the description into a series and indicator value.
  - ArchivesSpace indicators are different from the other profiles, as they are formatted like "123XYZ" or "XYZ-123". The script parses the indicator into a series and indicator (alphabetic and numerical, respectively), which are matched against the corresponding Alma data.
  - Normalization is applied to the Alma indicator to uppercase the series, remove leading zeroes, and remove trailing " RESTRICTED" text.
  - e.g. Alma description "ser.P box.0011 RESTRICTED" will match ASpace top container with indicator "11P" or "P-11".

### Determining the correct profile

When barcoding a new collection, it is useful to first look at sample records in Alma and ArchivesSpace. If Alma descriptions are formatted like "box.001", then `indicator_type_matching.py` is a good place to start. If after a test run you find that there are a lot of records with matching indicators but different types (e.g. Alma has "box.001" through "box.100", while ArchivesSpace has folders 1-100 and no boxes in this range), then `indicator_only_matching.py` is likely the correct profile.

If Alma descriptions are formatted like "ser.P box.0011" and ArchivesSpace indicators are formatted like "123XYZ" or "XYZ-123", then `series_description_matching.py` is likely the correct profile.

When determining which profile to use for a new collection, it easiest and safest to run the script locally using a copy of the ArchivesSpace database, with or without the `--dry_run` flag.

We have only barcoded a few collections so far, so the profiles may need to be adjusted as we encounter new data.

### Evaluating the script output

After running the script, you will see several output files:
- `add_alma_barcodes_to_archivesspace_{timestamp}.log`: The main log file.
- `unhandled_add_alma_barcodes_to_archivesspace_{timestamp}.json`: A JSON file containing Alma items that were not matched to ArchivesSpace top containers.
- `aspace_data_{resource_id}.json`: A JSON file containing the ArchivesSpace top container data for the resource. This file is used as a data source for the script if the `--use_cache` flag is set.
- `alma_data_{bib_id}_{holdings_id}.json`: A JSON file containing the Alma item data for the collection. This file is used as a data source for the script if the `--use_cache` flag is set.

The first two of these files are important for evaluating the script output. The log file ends with a summary statement describing the total number of Alma and ArchivesSpace records processed, the number of matches and non-matches, and the number of items with duplicate keys. If more than 80% of the items from each source are matched, the configuration profile is likely correct and the data is clean enough to run the script in the production environment.

The unhandled items file contains five types of potential issues:
- `unmatched_alma_items`: Alma items that were not matched to ArchivesSpace top containers. 
- `unmatched_aspace_containers`: ArchivesSpace top containers that were not matched to Alma items. 
- `top_containers_with_barcode`: ArchivesSpace top containers that already had a barcode before running the script.
- `items_with_duplicate_keys`: Alma items that have the same key as another item in the collection. For example, "box.001" and "box.1" would be considered duplicates when using the `indicator_type_matching.py` profile. 
- `top_containers_with_duplicate_keys`: ArchivesSpace top containers that have the same key as another top container in the collection. For example, "11P" and "P-11" would be considered duplicates when using the `series_description_matching.py` profile.

### Updating barcodes in hosted ArchivesSpace (Test and Production)

UCLA has two hosted ArchivesSpace instances: test (https://uclalsc-test.lyrasistechnology.org/) and production (https://uclalsc-staff.lyrasistechnology.org/). 

To use the script to update barcodes in the test or production ArchivesSpace instances, run the script from the support server, `p-u-exlsupport01.library.ucla.edu`, with the appropriate configuration file: `.archivessnake_secret_TEST.yml` for test and `.archivessnake_secret_PROD.yml` for production. Unlike the local setup, the script runs directly on the support server, not in a Docker container.
Ask a teammate if you need help accessing the support server. 

Alternatively, you can run the script against the test instance from your local machine by setting up a tunneled connection. See the "Connecting to remote instances (UCLA only)" section below for more information. You will need to create a new API configuration file (copy of `.archivessnake.yml`), with an updated `baseurl` of `https://uclalsc-test.lyrasistechnology.org:9000/api` and other credentials as appropriate - ask a teammate if you need these.

### Getting counts of containers by resource ID

The Airtable data provided by LSC can be extended with counts of the containers related to each `Rec ID` using `get_container_counts.py`, in order to get a sense of which collections are largest, and thus which to prioritize for the barcoding process.

The script accepts two arguments:
1. `--file_name`: path to the CSV export of LSC's Airtable data
2. `--config_file`: path to the YAML configuration file, which includes database credentials for the target ArchivesSpace instance

With a `bash` session open in the `python` container, run `python get_container_counts.py --file_name {{PATH_TO_LSC_DATA}} --config_file {{PATH_TO_CONFIG_YAML}}`.

## Rebuilding Solr Index

TBD - all I know for now is this is a long-running process (14 hours so far....)

## Using APIs

All API access is handled by the main application service, `archivesspace`, on port 8089.  This can be reached from the python container.
`curl` example, for now:
```
# Open bash session on python container
docker compose exec python bash

# Authenticate
curl -s -F password="admin" "http://archivesspace:8089/users/admin/login"

# Use the session key for all other API requests
curl -H "X-ArchivesSpace-Session: your_session_key" "http://archivesspace:8089/repositories"
```

## Connecting to remote instances (UCLA only)

It's possible to access data "live" in the hosted test instance, from the development environment. This requires extra
setup, because the APIs are IP-restricted and must be accessed via HTTPS/TLS.  General notes are in our [internal documentation](https://uclalibrary.atlassian.net/wiki/x/1gOWGg).  For this specific application:

1. Add `127.0.0.1       uclalsc-test.lyrasistechnology.org` to your local `/etc/hosts`
2. Create a tunnel from local machine through our jump server. Local port is arbitrary, I've used `9000`:

   `ssh -NT -L 0.0.0.0:9000:uclalsc-test.lyrasistechnology.org:443 jump`
3. Connect from local machine, or from within Docker, using `https://uclalsc-test.lyrasistechnology.org:9000/api` and appropriate credentials.

## Accessing hosted databases (UCLA only)

When running against hosted systems (UCLA's test and production ArchivesSpace instances), some APIs can time out (for example, getting top containers for a resource, where some of our resources have thousands of containers). An alternative is to read data from the database, manipulate it as needed, then call APIs as usual to update data.

The hosted databases are IP-restricted, and must be accessed via tunneled connections.  To set up the connections, run one of the following on the support server we use, `p-u-exlsupport01.library.ucla.edu`:
```
# Connect to TEST database
### TBD - waiting for vendor to set this up ###

# Connect to PROD database
ssh -i ~/.ssh/id_aspace_ssh -NT -L \
3306:aspace-hosting-production-db-shared-p1.lyrtech.org:3306 \
ucla-kohler@aspace-hosting-production-bastion.lyrtech.org
```
This tunnel runs in the foreground, so you'll need to open a second connection to run programs which need it.

