"""
Utility functions and helpers for working with ArchivesSpace.

This module provides utilities for interacting with ArchivesSpace
that can be reused across multiple scripts in the toolkit.
"""

import csv
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from asnake.client import ASnakeClient
from MySQLdb import connect
from MySQLdb.cursors import DictCursor
from utils.generic_utils import read_from_cache, write_json_output


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


def get_ao_uris_by_external_id_source_from_db(
    db_settings: dict, repo_id: int, source_prefix: str
) -> list[str]:
    """Returns the URIs of archival objects in a repository that have at least one
    External ID whose source starts with source_prefix, obtained via database query.
    This is intended as an alternative to paging through every archival object in
    a repository via the API, which is slow for large repositories.

    Matching uses SQL LIKE, which is case-insensitive under MySQL's default
    collations, so results may include sources that differ only in case. Callers
    should re-check each record's sources before changing anything.

    :param dict db_settings: A dict with DB connection details.
    :param int repo_id: ASpace repository ID to search.
    :param str source_prefix: Leading text of the External ID source to match.
    :return: A list of archival object URIs, ordered by ID.
    """

    mysql_client = connect(
        host=db_settings.get("host"),
        database=db_settings.get("database"),
        user=db_settings.get("user"),
        password=db_settings.get("password"),
    )

    query = """
        select distinct
            ao.id,
            concat('/repositories/', ao.repo_id, '/archival_objects/', ao.id) as ao_uri
        from external_id ei
        inner join archival_object ao on ei.archival_object_id = ao.id
        where ao.repo_id = %s
        and ei.source like %s
        order by ao.id
    """
    # Parameterized query requires tuple of values
    cursor = mysql_client.cursor(DictCursor)
    cursor.execute(query, (repo_id, f"{source_prefix}%"))
    ao_uris = [row["ao_uri"] for row in cursor.fetchall()]
    cursor.close()
    mysql_client.close()
    return ao_uris


def get_ao_external_ids_by_source_from_db(
    db_settings: dict, repo_id: int, source_prefix: str
) -> list[dict]:
    """Returns one row per External ID, on archival objects in a repository, whose
    source starts with source_prefix, with enough archival object context for a
    report. Obtained via a single database query, as a fast alternative to
    fetching each archival object via the API.

    Matching uses SQL LIKE, which is case-insensitive under MySQL's default
    collations, so results may include sources that differ only in case. Callers
    should re-check each row's source.
    :param dict db_settings: A dict with DB connection details.
    :param int repo_id: ASpace repository ID to search.
    :param str source_prefix: Leading text of the External ID source to match.
    :return: A list of dicts with keys ao_uri, ao_ref_id, ao_title, level,
        resource_uri, source, removed_value; ordered by archival object ID.
    """
    mysql_client = connect(
        host=db_settings.get("host"),
        database=db_settings.get("database"),
        user=db_settings.get("user"),
        password=db_settings.get("password"),
    )

    query = """
        select
            concat('/repositories/', ao.repo_id, '/archival_objects/', ao.id) as ao_uri,
            ao.ref_id as ao_ref_id,
            coalesce(ao.title, ao.display_string) as ao_title,
            ev.value as level,
            concat('/repositories/', ao.repo_id, '/resources/', ao.root_record_id)
                as resource_uri,
            ei.source,
            ei.external_id as removed_value
        from external_id ei
        inner join archival_object ao on ei.archival_object_id = ao.id
        left join enumeration_value ev on ao.level_id = ev.id
        where ao.repo_id = %s
        and ei.source like %s
        order by ao.id, ei.id
    """
    # Parameterized query requires tuple of values
    cursor = mysql_client.cursor(DictCursor)
    cursor.execute(query, (repo_id, f"{source_prefix}%"))
    rows = list(cursor.fetchall())
    cursor.close()
    mysql_client.close()
    return rows


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
    ids_to_add: list[tuple[str, str]],
    sources_to_remove: set[str],
) -> list[dict]:
    """Update a resource's repeatable external_ids subrecord in place.

    A resource may legitimately have more than one external_id entry for the
    same source (e.g. more than one MMS ID or OCLC number linked to a single
    collection), so entries are matched on the (source, value) pair, not on
    source alone. For each (source, value) pair in ids_to_add, an entry is
    appended only if that exact pair doesn't already exist.

    Any existing entry whose source is in sources_to_remove is dropped
    entirely. Entries with any other source, and any (source, value) pairs
    already present, are left as-is.

    Mutates resource in place. Returns a list of change records for logging,
    each a dict with keys: source, action ("added" | "removed"), before,
    after, resource_identifier, resource_uri.

    :param dict resource: ASpace resource dict to update.
    :param list[tuple[str, str]] ids_to_add: (source, external_id value) pairs
        to ensure are present. May include more than one pair for the same
        source.
    :param set[str] sources_to_remove: Source values whose entries should be
        removed entirely.
    :return: List of change record dicts describing what was added/removed,
        including the resource identifier and URI for logging.
    """
    external_ids = resource.get("external_ids", [])
    changes: list[dict] = []

    # Drop entries for any source being fully removed (e.g. legacy AT source)
    # before checking what's already present, so a removed source doesn't
    # block re-adding a value under a different source.
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

    existing_pairs = {(eid.get("source"), eid.get("external_id")) for eid in kept_ids}

    for source, value in ids_to_add:
        if (source, value) in existing_pairs:
            # Already present (e.g. a second CSV row repeating a pair already
            # added by an earlier row this run) - nothing to do.
            continue
        kept_ids.append(
            {
                "jsonmodel_type": "external_id",
                "source": source,
                "external_id": value,
            }
        )
        changes.append(
            {"source": source, "action": "added", "before": None, "after": value}
        )
        existing_pairs.add((source, value))

    resource["external_ids"] = kept_ids

    # Add resource identifier and URI to each change record for logging.
    for change in changes:
        # Use resource title if available, otherwise fallback to URI.
        change["resource_identifier"] = resource.get("title") or resource.get("uri")
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
        write_json_output(containers, aspace_cache_file, write_cache=True)
    return containers


# "FALSE DUPLICATE" TOP CONTAINERS
#
# A false duplicate is a top container that shares a key (e.g. "Box 1") with a real
# container, but is really a placeholder linked to backlog or accession material.
# False duplicates should be reported, but never merged or given migrated Alma data.
#
# A container is a false duplicate if the title of a directly linked archival object
# (usually a File), or of that AO's immediate parent (usually a Series), contains
# one of these terms as a whole word, in any capitalization.

FALSE_DUPLICATE_TERMS: dict[str, re.Pattern] = {
    "backlog": re.compile(r"\bbacklog\b", re.IGNORECASE),
    # Matches "Accession", "accession", "ACCESSION" anywhere in a title, but not
    # "deaccession", "accessions", "accessioned", "accessioning", etc.
    "accession": re.compile(r"\baccession\b", re.IGNORECASE),
}


def get_false_duplicate_reason(linked_titles: list[tuple[str, str]]) -> str | None:
    """Returns the reason a top container is a false duplicate, based on the titles
    of its linked AOs and their parents, or None if it is not a false duplicate.

    :param list[tuple[str, str]] linked_titles: (source, title) pairs, where source
        describes where the title came from (e.g. "Linked AO", "Parent AO").
    :return: Reason string naming the source, term, and matched title, or None.
    """
    for term, pattern in FALSE_DUPLICATE_TERMS.items():
        for source, title in linked_titles:
            if title and pattern.search(title):
                return f"{source} title contains '{term}': {title}"
    return None


def get_linked_titles_for_top_containers_from_db(
    db_settings: dict, top_container_ids: list[int]
) -> dict[int, list[tuple[str, str]]]:
    """Returns titles of the archival objects directly linked to the given top
    containers, and of each of those AOs' immediate parents, via a single database query.

    Unlike other queries in this module, this does NOT filter on publish/suppressed
    status: placeholder AOs for backlog and accession material are usually unpublished,
    and must still be detected.

    :param dict db_settings: A dict with DB connection details.
    :param list[int] top_container_ids: ASpace top container IDs.
    :return: Dict mapping top container ID to a list of (source, title) pairs,
        where source is "Linked AO" or "Parent AO". Every requested ID is present,
        possibly with an empty list.
    """
    titles: dict[int, list[tuple[str, str]]] = {
        tc_id: [] for tc_id in top_container_ids
    }
    if not top_container_ids:
        return titles

    mysql_client = connect(
        host=db_settings.get("host"),
        database=db_settings.get("database"),
        user=db_settings.get("user"),
        password=db_settings.get("password"),
    )
    # One placeholder per ID, for a safe parameterized IN clause.
    placeholders = ", ".join(["%s"] * len(top_container_ids))
    query = f"""
        select distinct tclr.top_container_id as tc_id,
            'Linked AO' as source, ao.title as title
        from top_container_link_rlshp tclr
        inner join sub_container sc on tclr.sub_container_id = sc.id
        inner join instance i on sc.instance_id = i.id
        inner join archival_object ao on i.archival_object_id = ao.id
        where tclr.top_container_id in ({placeholders})
        union
        select distinct tclr.top_container_id as tc_id,
            'Parent AO' as source, parent.title as title
        from top_container_link_rlshp tclr
        inner join sub_container sc on tclr.sub_container_id = sc.id
        inner join instance i on sc.instance_id = i.id
        inner join archival_object ao on i.archival_object_id = ao.id
        inner join archival_object parent on ao.parent_id = parent.id
        where tclr.top_container_id in ({placeholders})
        order by tc_id, source, title
    """
    cursor = mysql_client.cursor(DictCursor)
    cursor.execute(query, tuple(top_container_ids) * 2)
    for row in cursor.fetchall():
        titles[int(row["tc_id"])].append((row["source"], row["title"]))
    cursor.close()
    mysql_client.close()
    return titles


def get_top_container_id(top_container_uri: str) -> int:
    """Returns the numeric ID from a top container URI,
    e.g. /repositories/2/top_containers/123 -> 123.

    :param str top_container_uri: Top container URI.
    :return: Top container ID.
    """
    return int(top_container_uri.rstrip("/").split("/")[-1])


def get_required_db_settings(config: dict) -> dict:
    """Returns the database settings from a loaded config file, or raises an error
    if they are missing. For use by scripts that need the database for correct results.

    :param dict config: Loaded YAML config.
    :return: Database settings dict.
    :raises ValueError: If the config has no `database` settings.
    """
    db_settings = config.get("database")
    if not db_settings:
        raise ValueError(
            "Database connection settings (`database` in the config file) are required."
        )
    return db_settings


def find_false_duplicates(
    db_settings: dict, top_containers: list[dict]
) -> dict[str, str]:
    """Checks the given top containers for false duplicate status, using the database.

    The database is required: the API has no endpoint for the archival objects linked
    to a top container (the container's `series` field lists only top-level series).

    :param dict db_settings: A dict with DB connection details.
    :param list[dict] top_containers: Top container dicts (only `uri` is used).
    :return: Dict mapping URI to reason, for false duplicates only.
    """
    tc_uris = {
        get_top_container_id(tc.get("uri", "")): tc.get("uri") for tc in top_containers
    }
    titles_by_id = get_linked_titles_for_top_containers_from_db(
        db_settings, list(tc_uris.keys())
    )
    false_duplicates: dict[str, str] = {}
    for tc_id, titles in titles_by_id.items():
        reason = get_false_duplicate_reason(titles)
        if reason:
            uri = tc_uris.get(tc_id)
            # URI will always be present. Type checker doesn't know this, so add a guard here.
            if uri is None:
                raise ValueError(f"Expected URI for top container ID {tc_id}.")
            false_duplicates[uri] = reason
    return false_duplicates


def _get_uri_from_duplicate_entry(entry: dict | tuple) -> str:
    """Matching profiles report duplicate containers either as full dicts
    or as tuples whose first element is the URI; return the URI either way."""
    # URI will always be present in the entry, whether it's a dict or a tuple.
    if isinstance(entry, dict):
        # URI will always be present in the dict - see matching profiles in `config/`.
        # For consistency, use .get() anyway and add a guard for the type checker.
        uri = entry.get("uri")
        if uri is None:
            raise ValueError("Expected 'uri' key in duplicate entry dict.")
        return uri
    return entry[0]


def exclude_false_duplicates(
    db_settings: dict,
    top_containers: list[dict],
    get_aspace_match_data,
    logger=None,
) -> tuple[list[dict], list[dict]]:
    """Removes false duplicates from a list of top containers before matching.

    Only containers that share a matching key with another container (according to
    the given profile's `get_aspace_match_data`) are checked, which keeps lookups
    to a minimum. A placeholder whose key is unique is not a false duplicate,
    and is left in place.

    :param dict db_settings: A dict with DB connection details.
    :param list[dict] top_containers: Full top container dicts.
    :param get_aspace_match_data: The matching profile's get_aspace_match_data function.
    :param logger: Optional logger.
    :return: Tuple of (containers to use for matching,
        excluded false duplicates as report dicts with uri, type, indicator, reason).
    """
    # Dry pass through the profile, without logging, just to find duplicate keys.
    _, duplicate_entries = get_aspace_match_data(top_containers, None)
    duplicate_uris = {_get_uri_from_duplicate_entry(e) for e in duplicate_entries}
    if not duplicate_uris:
        return top_containers, []

    candidates = [tc for tc in top_containers if tc.get("uri") in duplicate_uris]
    false_duplicates = find_false_duplicates(db_settings, candidates)

    kept = []
    excluded = []
    for tc in top_containers:
        uri = tc.get("uri")
        reason = false_duplicates.get(uri) if uri else None
        if reason:
            if logger:
                logger.info(
                    f"Excluding false duplicate top container {uri} "
                    f"({tc.get('type')} {tc.get('indicator')}): {reason}"
                )
            excluded.append(
                {
                    "uri": uri,
                    "type": tc.get("type"),
                    "indicator": tc.get("indicator"),
                    "reason": reason,
                }
            )
        else:
            kept.append(tc)
    return kept, excluded


# MARCXML export and OCLC External IDs for resources


# External ID source for OCLC numbers on ASpace resources. This must match
# OCLC_SOURCE in update_aspace_external_ids.py, which writes these values.
OCLC_SOURCE = "OCLC"

# Namespace of the MARCXML produced by ASpace's MARC 21 exporter.
MARCXML_NAMESPACE = "http://www.loc.gov/MARC21/slim"

# Default name of the CSV column holding resource IDs, for
# read_resource_ids_from_csv().
RESOURCE_ID_CSV_COLUMN = "resource_id"


@dataclass
class ResourceMarcxml:
    """MARCXML export and identifiers for one ASpace resource.

    Exactly one of marcxml and error is set. identifier, title and oclc_ids are filled in whenever
    the resource record itself could be fetched, even if its MARCXML export then failed.
    """

    resource_id: int
    uri: str
    identifier: str | None = None
    title: str | None = None
    oclc_ids: list[str] = field(default_factory=list)
    marcxml: str | None = None
    error: str | None = None


def read_resource_ids_from_csv(
    csv_path: str, column: str = RESOURCE_ID_CSV_COLUMN
) -> list[int]:
    """Read ASpace resource IDs from one column of a CSV file.

    Values must be integer resource IDs (e.g. "1234"), not URIs. Leading and trailing whitespace
    is ignored. Duplicate IDs are returned as-is, in file order.

    :param str csv_path: Path to the CSV file.
    :param str column: Name of the column holding resource IDs.
    :return: List of resource IDs.
    :raises ValueError: If the column is missing, or any value in it is blank
        or not an integer. The message lists every bad row, so they can all
        be fixed at once.
    """
    with open(csv_path, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if column not in (reader.fieldnames or []):
            raise ValueError(
                f"Input CSV has no '{column}' column. "
                f"Found columns: {reader.fieldnames}"
            )

        resource_ids = []
        bad_rows = []
        for row in reader:
            value = (row.get(column) or "").strip()
            # isascii() also rules out non-ASCII digits, which int() accepts.
            if value.isascii() and value.isdigit():
                resource_ids.append(int(value))
            else:
                # line_num is the file line just read, so it stays accurate
                # when the reader skips blank lines.
                bad_rows.append(f"line {reader.line_num}: {value!r}")

    if bad_rows:
        raise ValueError(
            f"Input CSV has blank or non-integer values in '{column}': "
            + "; ".join(bad_rows)
        )
    return resource_ids


def get_resource_identifier(resource: dict) -> str | None:
    """Return a resource's collection identifier as a single string.

    ASpace stores the identifier in up to four parts (id_0 to id_3).
    Parts that are present are joined with "-".

    :param dict resource: ASpace resource dict.
    :return: The identifier, or None if the resource has no identifier parts.
    """
    parts = [resource.get(f"id_{i}") for i in range(4)]
    return "-".join(part for part in parts if part) or None


def get_resource_external_ids_by_source(resource: dict, source: str) -> list[str]:
    """Return all of a resource's External ID values with the given source.

    A resource can have more than one External ID with the same source,
    so this returns all of them, in record order. Entries with no value are skipped.

    :param dict resource: ASpace resource dict.
    :param str source: External ID source to match exactly, e.g. OCLC_SOURCE.
    :return: List of External ID values.
    """
    return [
        eid["external_id"]
        for eid in resource.get("external_ids", [])
        if eid.get("source") == source and eid.get("external_id")
    ]


def get_resource_marcxml(
    aspace_client: ASnakeClient,
    repo_id: int,
    resource_id: int,
    include_unpublished: bool = False,
) -> tuple[str | None, str | None]:
    """Export a resource as MARCXML and check that the result is well-formed.

    Calls ASpace's MARC 21 export endpoint,
    GET /repositories/:repo_id/resources/marc21/:id.xml.
    include_unpublished is passed as the endpoint's include_unpublished_marc
    parameter, whose ASpace default is false. In the public ASpace 4.2.1
    source, this controls whether unpublished content (e.g. notes) is
    included in the export; it does not stop an unpublished resource from
    being exported.

    Network errors (e.g. a dropped connection) are not caught here, since
    they would affect every resource, not just this one.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param int repo_id: The ArchivesSpace repository ID.
    :param int resource_id: The ID of the resource to export.
    :param bool include_unpublished: Whether to include unpublished content.
    :return: (marcxml, None) on success, or (None, error message) if the
        export request fails or its response is not well-formed MARCXML.
    """
    response = aspace_client.get(
        f"/repositories/{repo_id}/resources/marc21/{resource_id}.xml",
        params={"include_unpublished_marc": str(include_unpublished).lower()},
    )
    if response.status_code != 200:
        # ASnake's own logging shows only the status, not the response body,
        # so include the body (truncated) in the error message.
        return None, (
            f"MARCXML export failed with HTTP {response.status_code}: "
            f"{response.text[:500]}"
        )

    # Check the raw bytes rather than response.text: ASpace's Content-Type
    # header has no charset, so requests would have to guess the encoding.
    # ElementTree uses the XML declaration's encoding (UTF-8 from ASpace's
    # exporter), and _get_marcxml_problem() confirms the bytes are UTF-8.
    problem = _get_marcxml_problem(response.content)
    if problem:
        return None, f"MARCXML export is malformed: {problem}"
    return response.content.decode("utf-8"), None


def _get_marcxml_problem(content: bytes) -> str | None:
    """Check that content is well-formed MARCXML for a single record.

    The check is structural only: the content must parse as XML, its root
    must be a MARCXML <collection> or <record>, and it must contain exactly
    one <record>, since ASpace exports one MARC record per resource.
    Field contents are not checked.

    :param bytes content: Raw response body.
    :return: None if the content passes, or a description of the problem.
    """
    try:
        root = ET.fromstring(content)
        # Also confirm the bytes decode as UTF-8.
        content.decode("utf-8")
    except ET.ParseError as e:
        return f"not well-formed XML ({e})"
    except UnicodeDecodeError as e:
        return f"not valid UTF-8 ({e})"

    record_tag = f"{{{MARCXML_NAMESPACE}}}record"
    collection_tag = f"{{{MARCXML_NAMESPACE}}}collection"
    if root.tag == record_tag:
        record_count = 1
    elif root.tag == collection_tag:
        record_count = len(root.findall(record_tag))
    else:
        return f"root element is {root.tag}, not a MARCXML collection or record"

    if record_count != 1:
        return f"expected 1 MARC record, found {record_count}"
    return None


def get_marcxml_for_resources(
    aspace_client: ASnakeClient,
    resource_ids: list[int],
    repo_id: int = 2,
    include_unpublished: bool = False,
) -> list[ResourceMarcxml]:
    """Get MARCXML, plus the identifiers the OCLC scripts need, for a list
    of ASpace resources.

    For each resource, fetches the resource record (for its URI, collection
    identifier, title and OCLC External IDs) and its MARCXML export. A
    resource whose record can't be fetched, or whose export fails or is
    malformed, gets an error in its result; the rest of the list is still
    processed. Nothing is logged here, so calling scripts should log or
    report errors from the results.

    :param ASnakeClient aspace_client: ASnakeClient instance.
    :param list[int] resource_ids: IDs of the resources to export.
    :param int repo_id: The ArchivesSpace repository ID. Defaults to 2.
    :param bool include_unpublished: Passed to get_resource_marcxml().
    :return: One ResourceMarcxml per resource ID, in input order.
    """
    results = []
    for resource_id in resource_ids:
        result = ResourceMarcxml(
            resource_id=resource_id,
            uri=f"/repositories/{repo_id}/resources/{resource_id}",
        )
        results.append(result)

        resource = get_resource_by_uri(aspace_client, result.uri)
        if resource is None:
            result.error = (
                "Could not fetch resource record (not found, or request failed)"
            )
            continue

        result.identifier = get_resource_identifier(resource)
        result.title = resource.get("title")
        result.oclc_ids = get_resource_external_ids_by_source(resource, OCLC_SOURCE)
        result.marcxml, result.error = get_resource_marcxml(
            aspace_client, repo_id, resource_id, include_unpublished
        )
    return results
