"""
Utility functions and helpers for working with ArchivesSpace.

This module provides utilities for interacting with ArchivesSpace
that can be reused across multiple scripts in the toolkit.
"""

from asnake.client import ASnakeClient
from MySQLdb import connect
from MySQLdb.cursors import DictCursor
from utils.generic_utils import read_from_cache, write_to_cache


def get_container_refs_from_api(
    aspace_client: ASnakeClient, repo_id: int, resource_id: int
) -> set[str]:
    """Returns a de-duped set of _ref_ top container URIs for the given resource_id,
    obtained via API call.
    This API call can fail via timeout in hosted environments, when
    more than a few thousand containers are associated with the resource.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param int repo_id: ASpace repository ID from which to retrieve containers.
    :param int resource_id: ASpace resource ID for target collection.
    :return: A set of container refs.
    """
    url = f"/repositories/{repo_id}/resources/{resource_id}/top_containers"
    container_refs = aspace_client.get(url).json()
    # Extract the ref URIs and de-dup.
    return set(tc["ref"] for tc in container_refs)


def get_container_refs_from_db(db_settings: dict, resource_id: int) -> set[str]:
    """Returns a de-duped set of _ref_ top container URIs for the given resource_id,
    obtained via database query.
    This is intended as an alternative for resources with more than a few thousand
    containers, as the API call may time out.

    :param dict db_settings: A dict with DB connection details.
    :param int resource_id: ASpace resource ID for target collection.
    :return: A set of container refs.
    """
    mysql_client = connect(
        host=db_settings.get("host"),
        database=db_settings.get("database"),
        user=db_settings.get("user"),
        password=db_settings.get("password"),
    )

    query = """
        select distinct
            concat('/repositories/', r.repo_id, '/top_containers/', tc.id) as container_uri
        from resource r
        inner join archival_object ao on r.id = ao.root_record_id
        inner join instance i on ao.id = i.archival_object_id
        inner join sub_container sc on i.id = sc.instance_id
        inner join top_container_link_rlshp tclr on sc.id = tclr.sub_container_id
        inner join top_container tc on tclr.top_container_id = tc.id
        where r.id = %s
        and ao.publish = 1 -- true
        and ao.suppressed = 0 -- false
        order by container_uri
    """
    # Parameterized query requires tuple of values
    cursor = mysql_client.cursor(DictCursor)
    cursor.execute(query, (resource_id,))
    container_refs = set(row["container_uri"] for row in cursor.fetchall())
    cursor.close()
    mysql_client.close()
    return container_refs


def get_ao_refs_for_top_container_from_db(
    db_settings: dict,
    top_container_id: int,
) -> list[str]:
    """Return de-duped archival object refs linked to the given top container ID
    via a database query. Filters for published and non-suppressed archival objects.

    :param dict db_settings: A dict with DB connection details.
    :param int top_container_id: ASpace top container ID.
    :return: A list of archival object refs.
    """
    mysql_client = connect(
        host=db_settings.get("host"),
        database=db_settings.get("database"),
        user=db_settings.get("user"),
        password=db_settings.get("password"),
    )

    # This adapts the query used in `_get_container_refs_from_db` to return
    # the set of archival object refs linked to the given top container.
    query = """
        select distinct
            concat('/repositories/', r.repo_id, '/archival_objects/', ao.id) as ao_uri
        from resource r
        inner join archival_object ao on r.id = ao.root_record_id
        inner join instance i on ao.id = i.archival_object_id
        inner join sub_container sc on i.id = sc.instance_id
        inner join top_container_link_rlshp tclr on sc.id = tclr.sub_container_id
        inner join top_container tc on tclr.top_container_id = tc.id
        where tc.id = %s
        and ao.publish = 1
        and ao.suppressed = 0
        order by ao_uri
    """

    cursor = mysql_client.cursor(DictCursor)
    cursor.execute(query, (top_container_id,))
    ao_refs = [row["ao_uri"] for row in cursor.fetchall()]
    cursor.close()
    mysql_client.close()
    return ao_refs


def get_ao_titles_for_top_container_from_db(
    db_settings: dict,
    top_container_id: int,
) -> list[str]:
    """Return de-duped archival object titles linked to the given top container ID
    via a database query. Filters for published and non-suppressed archival objects.

    :param dict db_settings: A dict with DB connection details.
    :param int top_container_id: ASpace top container ID.
    :return: A list of archival object titles.
    """
    mysql_client = connect(
        host=db_settings.get("host"),
        database=db_settings.get("database"),
        user=db_settings.get("user"),
        password=db_settings.get("password"),
    )

    # Same join structure as `get_ao_refs_for_top_container_from_db`,
    # selecting title instead of building a ref URI.
    query = """
        select distinct
            ao.title as ao_title
        from resource r
        inner join archival_object ao on r.id = ao.root_record_id
        inner join instance i on ao.id = i.archival_object_id
        inner join sub_container sc on i.id = sc.instance_id
        inner join top_container_link_rlshp tclr on sc.id = tclr.sub_container_id
        inner join top_container tc on tclr.top_container_id = tc.id
        where tc.id = %s
        and ao.publish = 1
        and ao.suppressed = 0
        order by ao_title
    """

    cursor = mysql_client.cursor(DictCursor)
    cursor.execute(query, (top_container_id,))
    ao_titles = [row["ao_title"] or "" for row in cursor.fetchall()]
    cursor.close()
    mysql_client.close()
    return ao_titles


def _get_containers_from_container_refs(
    aspace_client: ASnakeClient, container_refs: set[str]
) -> list[dict]:
    """Returns a list of full container dicts for the given refs,
    filtered to those linked to a published resource.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param set[str] container_refs: A set of top container ref URIs.
    :return: A list of container dicts.
    """
    containers = []
    for ref in container_refs:
        tc_json: dict = aspace_client.get(ref).json()
        if not tc_json.get("is_linked_to_published_record"):
            # Skip containers that are not linked to a published resource.
            continue
        containers.append(tc_json)
    return containers


def get_resource_by_uri(aspace_client: ASnakeClient, uri: str) -> dict | None:
    """Fetch a full ASpace resource record by its URI (ref), e.g.
    "/repositories/2/resources/123".

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param str uri: The resource's ASpace URI.
    :return: Full resource dict, or None if the URI could not be resolved.
    """
    response = aspace_client.get(uri)
    if response.status_code != 200:
        return None
    return response.json()


def update_external_ids(
    resource: dict,
    ids_to_set: dict[str, str],
    sources_to_remove: set[str],
) -> list[dict]:
    """Update a resource's repeatable external_ids subrecord in place.

    For each source in ids_to_set: adds a new external_id entry if no entry with
    that source exists yet, or updates the value in place if one does.
    Any existing entry whose source is in sources_to_remove is dropped.
    Entries with any other source are left as-is.

    Mutates resource in place. Returns a list of change records for logging,
    each a dict with keys: source, action ("added" | "updated" | "removed"),
    before, after.

    :param dict resource: ASpace resource dict to update.
    :param dict[str, str] ids_to_set: Mapping of source -> external_id value to
        add or update.
    :param set[str] sources_to_remove: Source values whose entries should be
        removed entirely.
    :return: List of change record dicts describing what was added/updated/removed.
    """
    external_ids = resource.get("external_ids", [])
    by_source = {eid.get("source"): eid for eid in external_ids}
    changes: list[dict] = []

    for source, value in ids_to_set.items():
        existing = by_source.get(source)
        if existing is None:
            external_ids.append(
                {
                    "jsonmodel_type": "external_id",
                    "source": source,
                    "external_id": value,
                }
            )
            changes.append(
                {"source": source, "action": "added", "before": None, "after": value}
            )
        elif existing.get("external_id") != value:
            before = existing.get("external_id")
            existing["external_id"] = value
            changes.append(
                {
                    "source": source,
                    "action": "updated",
                    "before": before,
                    "after": value,
                }
            )
        # else: value already matches, nothing to do.

    kept_ids = []
    for eid in external_ids:
        if eid.get("source") in sources_to_remove:
            changes.append(
                {
                    "source": eid.get("source"),
                    "action": "removed",
                    "before": eid.get("external_id"),
                    "after": None,
                }
            )
        else:
            kept_ids.append(eid)
    resource["external_ids"] = kept_ids

    # Add resource identifier and URI to each change record for logging.
    for change in changes:
        # Use resource title if available, otherwise fallback to resource ID.
        change["resource_identifier"] = resource.get("title") or resource.get("id")
        change["resource_uri"] = resource.get("uri")

    return changes


def get_aspace_containers(
    aspace_client: ASnakeClient,
    repo_id: int,
    resource_id: int,
    use_db: bool,
    use_cache: bool,
) -> list[dict]:
    """Returns full container data for all top containers linked to the given resource,
    using a cache file if available.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param int repo_id: ASpace repository ID.
    :param int resource_id: ASpace resource ID for target collection.
    :param bool use_db: If True, get container refs from DB instead of API.
    :param bool use_cache: If True, read from cache file if available.
    :return: A list of container dicts linked to published resources.
    """
    containers = None
    aspace_cache_file = f"aspace_data_{resource_id}.json"
    if use_cache:
        containers = read_from_cache(aspace_cache_file)
    if not containers:
        if use_db:
            db_settings = aspace_client.config.get("database")
            container_refs = get_container_refs_from_db(db_settings, resource_id)
        else:
            container_refs = get_container_refs_from_api(
                aspace_client, repo_id, resource_id
            )
        containers = _get_containers_from_container_refs(aspace_client, container_refs)
        write_to_cache(containers, aspace_cache_file)
    return containers
