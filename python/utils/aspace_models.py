"""
Classes that wrap ArchivesSpace records.

Each class stores a record's data as the ASpace API returns it, plus any related API
responses, and exposes the values scripts need as properties. Properties are computed
from the stored data each time they are read.

The classes never call the API themselves: functions in aspace_utils.py fetch the data
and build the objects. That keeps API access in one place, and lets tests build objects
directly from sample data.
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from requests import Response

# External ID source for OCLC numbers on ASpace resources. This must match
# OCLC_SOURCE in update_aspace_external_ids.py, which writes these values.
OCLC_SOURCE = "OCLC"

# Namespace of the MARCXML produced by ASpace's MARC 21 exporter.
MARCXML_NAMESPACE = "http://www.loc.gov/MARC21/slim"


@dataclass
class Resource:
    """An ArchivesSpace resource record, plus its MARCXML export if it was fetched.

    Build these with get_resource() or get_resources() in aspace_utils.py, or directly from sample
    data in tests. The repr does not include data and marcxml_response, so logging a Resource
    doesn't print the whole record.

    :param int resource_id: The resource's ID.
    :param int repo_id: The ID of the repository holding the resource. Defaults to 2.
    :param dict data: The resource record from the ASpace API, or {} if it wasn't found
        or hasn't been fetched.
    :param Response | None marcxml_response: The response from ASpace's MARC 21 export
        endpoint, or None if the export hasn't been fetched.
    """

    resource_id: int
    repo_id: int = 2
    data: dict[str, Any] = field(default_factory=dict, repr=False)
    marcxml_response: Response | None = field(default=None, repr=False)

    # Resource record

    @property
    def uri(self) -> str:
        """The resource's URI, e.g. /repositories/2/resources/123."""
        return f"/repositories/{self.repo_id}/resources/{self.resource_id}"

    @property
    def found(self) -> bool:
        """Whether the resource record is available."""
        return bool(self.data)

    @property
    def title(self) -> str:
        """The resource's title, or "" if not available."""
        return self.data.get("title", "")

    @property
    def identifier(self) -> str:
        """The collection identifier as a single string, or "" if there is none.

        ASpace stores the identifier in up to four parts (id_0 to id_3). As in ASpace's
        own search index, the parts that are present are joined with "-".
        """
        parts = [self.data.get(f"id_{i}", "") for i in range(4)]
        return "-".join(part for part in parts if part)

    @property
    def published(self) -> bool:
        """Whether the resource itself is published."""
        return bool(self.data.get("publish", False))

    @property
    def external_ids(self) -> list[tuple[str, str]]:
        """The resource's External IDs, as (source, value) pairs in record order.

        These are pairs, not a dict keyed by source, because a resource can have more
        than one External ID with the same source.
        """
        return [
            (eid.get("source", ""), eid.get("external_id", ""))
            for eid in self.data.get("external_ids", [])
        ]

    def external_ids_by_source(self, source: str) -> list[str]:
        """Return the values of all of the resource's External IDs with this source.

        :param str source: External ID source to match exactly, e.g. OCLC_SOURCE.
        :return: List of External ID values.
        """
        return [
            value for eid_source, value in self.external_ids if eid_source == source
        ]

    @property
    def oclc_ids(self) -> list[str]:
        """The values of the resource's OCLC External IDs, as stored."""
        return self.external_ids_by_source(OCLC_SOURCE)

    # MARCXML export

    @property
    def marcxml_error(self) -> str:
        """What's wrong with the MARCXML export, or "" if nothing is.

        Also "" when the export hasn't been fetched: use has_marcxml to tell that apart
        from a valid export.
        """
        response = self.marcxml_response
        if response is None:
            return ""
        if response.status_code != 200:
            # ASnake's own logging shows only the status, not the response body,
            # so include the body (truncated) here.
            return (
                f"MARCXML export failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        problem = self._find_marcxml_problem(response.content)
        if problem:
            return f"MARCXML export is malformed: {problem}"
        return ""

    @property
    def has_marcxml(self) -> bool:
        """Whether a valid MARCXML export is available."""
        return self.marcxml_response is not None and not self.marcxml_error

    @property
    def marcxml(self) -> str:
        """The MARCXML export as text, or "" if there's no valid export."""
        response = self.marcxml_response
        if response is None or self.marcxml_error:
            return ""
        return response.content.decode("utf-8")

    # Overall status

    @property
    def error(self) -> str:
        """The first problem with this resource, for reports, or "" if there's none.

        A missing record comes first; otherwise this is marcxml_error.
        """
        if not self.found:
            return "Resource record not available (not found, or request failed)"
        return self.marcxml_error

    @staticmethod
    def _find_marcxml_problem(content: bytes) -> str:
        """Check that content is well-formed MARCXML for a single record.

        The check is structural only: the content must parse as XML and decode as
        UTF-8, its root must be a MARCXML <collection> or <record>, and it must contain
        exactly one <record>, since ASpace exports one MARC record per resource. Field
        contents are not checked.

        :param bytes content: Raw response body.
        :return: A description of the problem, or "" if there is none.
        """
        try:
            root = ET.fromstring(content)
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
        return ""
