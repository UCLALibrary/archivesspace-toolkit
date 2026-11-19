import os
import tempfile
import unittest
from unittest.mock import MagicMock

from utils.aspace_utils import (
    OCLC_SOURCE,
    get_marcxml_for_resources,
    get_resource_external_ids_by_source,
    get_resource_identifier,
    get_resource_marcxml,
    read_resource_ids_from_csv,
)

# Minimal MARCXML in the shape ASpace's MARC 21 exporter produces:
# a <collection> in the MARC21 slim namespace, wrapping one <record>.
# The title includes non-ASCII characters, to check they survive decoding.
VALID_MARCXML = """<?xml version="1.0" encoding="UTF-8"?>
<collection xmlns="http://www.loc.gov/MARC21/slim"
    xmlns:marc="http://www.loc.gov/MARC21/slim">
  <record>
    <leader>00000npcaa2200000 u 4500</leader>
    <datafield tag="245" ind1="1" ind2="0">
      <subfield code="a">Papers of José Muñoz</subfield>
    </datafield>
  </record>
</collection>
""".encode("utf-8")


def _make_response(status_code: int = 200, content: bytes = b"", json_data=None):
    """Return a mock requests.Response with the given status and body."""
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.text = content.decode("utf-8", errors="replace")
    response.json.return_value = json_data
    return response


def _make_resource(resource_id: int, **overrides) -> dict:
    """Return a minimal ASpace resource dict, with optional overrides."""
    resource = {
        "uri": f"/repositories/2/resources/{resource_id}",
        "title": f"Collection {resource_id}",
        "id_0": f"MSS-{resource_id}",
        "external_ids": [{"source": OCLC_SOURCE, "external_id": f"ocn{resource_id}"}],
    }
    resource.update(overrides)
    return resource


class TestGetResourceIdentifier(unittest.TestCase):
    """Tests for get_resource_identifier()."""

    def test_single_part(self):
        self.assertEqual(get_resource_identifier({"id_0": "LSC.1234"}), "LSC.1234")

    def test_multiple_parts_joined_with_hyphen(self):
        resource = {"id_0": "MSS", "id_1": "1234", "id_2": "A"}
        self.assertEqual(get_resource_identifier(resource), "MSS-1234-A")

    def test_missing_and_empty_parts_skipped(self):
        resource = {"id_0": "MSS", "id_1": None, "id_2": "", "id_3": "B"}
        self.assertEqual(get_resource_identifier(resource), "MSS-B")

    def test_no_parts_returns_none(self):
        self.assertIsNone(get_resource_identifier({}))


class TestGetResourceExternalIdsBySource(unittest.TestCase):
    """Tests for get_resource_external_ids_by_source()."""

    def test_no_external_ids(self):
        self.assertEqual(get_resource_external_ids_by_source({}, OCLC_SOURCE), [])

    def test_returns_all_matching_values_in_order(self):
        resource = {
            "external_ids": [
                {"source": OCLC_SOURCE, "external_id": "111"},
                {"source": "ILS", "external_id": "9912345"},
                {"source": OCLC_SOURCE, "external_id": "(OCoLC)222"},
                {"source": "Archivists Toolkit Database::RESOURCE", "external_id": "9"},
            ]
        }
        self.assertEqual(
            get_resource_external_ids_by_source(resource, OCLC_SOURCE),
            ["111", "(OCoLC)222"],
        )

    def test_source_match_is_exact(self):
        resource = {"external_ids": [{"source": "oclc", "external_id": "111"}]}
        self.assertEqual(get_resource_external_ids_by_source(resource, OCLC_SOURCE), [])

    def test_entries_without_values_skipped(self):
        resource = {
            "external_ids": [
                {"source": OCLC_SOURCE},
                {"source": OCLC_SOURCE, "external_id": ""},
                {"source": OCLC_SOURCE, "external_id": "333"},
            ]
        }
        self.assertEqual(
            get_resource_external_ids_by_source(resource, OCLC_SOURCE), ["333"]
        )


class TestGetResourceMarcxml(unittest.TestCase):
    """Tests for get_resource_marcxml()."""

    def _get(self, content: bytes, status_code: int = 200, **kwargs):
        """Call get_resource_marcxml() with a client returning this response."""
        client = MagicMock()
        client.get.return_value = _make_response(status_code, content)
        result = get_resource_marcxml(client, 2, 123, **kwargs)
        return client, result

    def test_valid_export(self):
        client, (marcxml, error) = self._get(VALID_MARCXML)
        self.assertIsNone(error)
        self.assertEqual(marcxml, VALID_MARCXML.decode("utf-8"))
        self.assertIn("José Muñoz", marcxml)

    def test_calls_marc21_endpoint_with_params(self):
        client, _ = self._get(VALID_MARCXML)
        client.get.assert_called_once_with(
            "/repositories/2/resources/marc21/123.xml",
            params={"include_unpublished_marc": "false"},
        )
        client, _ = self._get(VALID_MARCXML, include_unpublished=True)
        client.get.assert_called_once_with(
            "/repositories/2/resources/marc21/123.xml",
            params={"include_unpublished_marc": "true"},
        )

    def test_record_as_root_is_accepted(self):
        content = b'<record xmlns="http://www.loc.gov/MARC21/slim"><leader/></record>'
        _, (marcxml, error) = self._get(content)
        self.assertIsNone(error)
        self.assertIsNotNone(marcxml)

    def test_http_error_includes_status_and_body(self):
        _, (marcxml, error) = self._get(b'{"error":"Resource not found"}', 404)
        self.assertIsNone(marcxml)
        self.assertIn("HTTP 404", error)
        self.assertIn("Resource not found", error)

    def test_not_well_formed_xml(self):
        _, (marcxml, error) = self._get(VALID_MARCXML.replace(b"</record>", b""))
        self.assertIsNone(marcxml)
        self.assertIn("not well-formed XML", error)


    def test_wrong_root_element(self):
        _, (marcxml, error) = self._get(b"<ead><record/></ead>")
        self.assertIsNone(marcxml)
        self.assertIn("not a MARCXML collection or record", error)

    def test_root_without_marc_namespace(self):
        _, (marcxml, error) = self._get(b"<collection><record/></collection>")
        self.assertIsNone(marcxml)
        self.assertIn("not a MARCXML collection or record", error)

    def test_collection_with_no_records(self):
        content = b'<collection xmlns="http://www.loc.gov/MARC21/slim"/>'
        _, (marcxml, error) = self._get(content)
        self.assertIsNone(marcxml)
        self.assertIn("expected 1 MARC record, found 0", error)

    def test_collection_with_two_records(self):
        content = (
            b'<collection xmlns="http://www.loc.gov/MARC21/slim">'
            b"<record/><record/></collection>"
        )
        _, (marcxml, error) = self._get(content)
        self.assertIsNone(marcxml)
        self.assertIn("expected 1 MARC record, found 2", error)


class TestGetMarcxmlForResources(unittest.TestCase):
    """Tests for get_marcxml_for_resources()."""

    def _make_client(self, resources: dict, exports: dict) -> MagicMock:
        """Return a mock ASnakeClient.

        :param dict resources: resource URI -> resource dict. URIs not listed
            return 404.
        :param dict exports: MARC export path -> (status code, content).
            Paths not listed return 404.
        """

        def get(path, params=None):
            if path in resources:
                return _make_response(200, json_data=resources[path])
            if path in exports:
                return _make_response(*exports[path])
            return _make_response(404, b'{"error":"Not found"}')

        client = MagicMock()
        client.get.side_effect = get
        return client

    def test_success(self):
        client = self._make_client(
            resources={"/repositories/2/resources/1": _make_resource(1)},
            exports={"/repositories/2/resources/marc21/1.xml": (200, VALID_MARCXML)},
        )
        [result] = get_marcxml_for_resources(client, [1])
        self.assertEqual(result.resource_id, 1)
        self.assertEqual(result.uri, "/repositories/2/resources/1")
        self.assertEqual(result.identifier, "MSS-1")
        self.assertEqual(result.title, "Collection 1")
        self.assertEqual(result.oclc_ids, ["ocn1"])
        self.assertEqual(result.marcxml, VALID_MARCXML.decode("utf-8"))
        self.assertIsNone(result.error)

    def test_resource_not_found_skips_export(self):
        client = self._make_client(resources={}, exports={})
        [result] = get_marcxml_for_resources(client, [99])
        self.assertEqual(result.uri, "/repositories/2/resources/99")
        self.assertIn("Could not fetch resource record", result.error)
        self.assertIsNone(result.marcxml)
        self.assertIsNone(result.identifier)
        # Only the resource fetch was attempted, not the MARC export.
        client.get.assert_called_once_with("/repositories/2/resources/99")

    def test_export_failure_keeps_resource_details(self):
        client = self._make_client(
            resources={"/repositories/2/resources/1": _make_resource(1)},
            exports={"/repositories/2/resources/marc21/1.xml": (500, b"Server error")},
        )
        [result] = get_marcxml_for_resources(client, [1])
        self.assertIsNone(result.marcxml)
        self.assertIn("HTTP 500", result.error)
        self.assertEqual(result.identifier, "MSS-1")
        self.assertEqual(result.oclc_ids, ["ocn1"])

    def test_failures_do_not_stop_batch_and_order_is_kept(self):
        client = self._make_client(
            resources={
                "/repositories/2/resources/1": _make_resource(1),
                "/repositories/2/resources/3": _make_resource(3, external_ids=[]),
            },
            exports={
                "/repositories/2/resources/marc21/1.xml": (200, b"<not-marc/>"),
                "/repositories/2/resources/marc21/3.xml": (200, VALID_MARCXML),
            },
        )
        results = get_marcxml_for_resources(client, [1, 2, 3])
        self.assertEqual([r.resource_id for r in results], [1, 2, 3])
        self.assertIn("malformed", results[0].error)
        self.assertIn("Could not fetch resource record", results[1].error)
        self.assertIsNone(results[2].error)
        self.assertEqual(results[2].oclc_ids, [])

    def test_repo_id_used_in_requests(self):
        client = self._make_client(
            resources={"/repositories/5/resources/1": _make_resource(1)},
            exports={"/repositories/5/resources/marc21/1.xml": (200, VALID_MARCXML)},
        )
        [result] = get_marcxml_for_resources(client, [1], repo_id=5)
        self.assertEqual(result.uri, "/repositories/5/resources/1")
        self.assertIsNone(result.error)

    def test_empty_list(self):
        client = self._make_client(resources={}, exports={})
        self.assertEqual(get_marcxml_for_resources(client, []), [])
        client.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()