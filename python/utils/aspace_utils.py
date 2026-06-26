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
