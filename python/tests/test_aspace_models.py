import unittest
from unittest.mock import MagicMock

from utils.aspace_models import OCLC_SOURCE, Resource

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


def make_response(
    status_code: int = 200, content: bytes = b"", json_data: dict | None = None
) -> MagicMock:
    """Return a mock requests.Response with the given status and body."""
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.text = content.decode("utf-8", errors="replace")
    response.json.return_value = json_data
    return response


class TestResourceRecord(unittest.TestCase):
    """Tests for the Resource properties that read the resource record."""

    def test_uri_built_from_ids(self):
        self.assertEqual(Resource(123).uri, "/repositories/2/resources/123")
        self.assertEqual(Resource(123, repo_id=5).uri, "/repositories/5/resources/123")

    def test_repr_leaves_out_data_and_response(self):
        resource = Resource(
            1, data={"title": "x" * 1000}, marcxml_response=make_response()
        )
        self.assertEqual(repr(resource), "Resource(resource_id=1, repo_id=2)")

    def test_found(self):
        self.assertFalse(Resource(1).found)
        self.assertTrue(Resource(1, data={"title": "Papers"}).found)

    def test_title(self):
        self.assertEqual(Resource(1, data={"title": "Papers"}).title, "Papers")
        self.assertEqual(Resource(1).title, "")

    def test_identifier_single_part(self):
        resource = Resource(1, data={"id_0": "LSC.1234"})
        self.assertEqual(resource.identifier, "LSC.1234")

    def test_identifier_parts_joined_with_hyphen(self):
        resource = Resource(1, data={"id_0": "MSS", "id_1": "1234", "id_2": "A"})
        self.assertEqual(resource.identifier, "MSS-1234-A")

    def test_identifier_missing_and_empty_parts_skipped(self):
        resource = Resource(
            1, data={"id_0": "MSS", "id_1": None, "id_2": "", "id_3": "B"}
        )
        self.assertEqual(resource.identifier, "MSS-B")

    def test_identifier_none(self):
        self.assertEqual(Resource(1).identifier, "")

    def test_published(self):
        self.assertTrue(Resource(1, data={"publish": True}).published)
        self.assertFalse(Resource(1, data={"publish": False}).published)
        self.assertFalse(Resource(1, data={"title": "Papers"}).published)


class TestResourceExternalIds(unittest.TestCase):
    """Tests for the Resource properties that read External IDs."""

    def setUp(self):
        self.resource = Resource(
            1,
            data={
                "external_ids": [
                    {"source": OCLC_SOURCE, "external_id": "111"},
                    {"source": "ILS", "external_id": "9912345"},
                    {"source": OCLC_SOURCE, "external_id": "222"},
                    {"source": "oclc", "external_id": "333"},
                ]
            },
        )

    def test_external_ids_are_pairs_in_record_order(self):
        self.assertEqual(
            self.resource.external_ids,
            [
                (OCLC_SOURCE, "111"),
                ("ILS", "9912345"),
                (OCLC_SOURCE, "222"),
                ("oclc", "333"),
            ],
        )

    def test_external_ids_by_source_returns_all_values_as_stored(self):
        self.assertEqual(self.resource.external_ids_by_source("ILS"), ["9912345"])
        self.assertEqual(
            self.resource.external_ids_by_source(OCLC_SOURCE), ["111", "222"]
        )

    def test_oclc_ids_match_source_exactly(self):
        # "oclc" in lower case is a different source.
        self.assertEqual(self.resource.oclc_ids, ["111", "222"])

    def test_no_external_ids(self):
        resource = Resource(1, data={"title": "Papers"})
        self.assertEqual(resource.external_ids, [])
        self.assertEqual(resource.oclc_ids, [])


class TestResourceMarcxml(unittest.TestCase):
    """Tests for the Resource properties that read the MARCXML export."""

    def _resource(self, content: bytes, status_code: int = 200) -> Resource:
        """Return a found Resource whose MARCXML export has this response."""
        return Resource(
            123,
            data={"title": "Papers"},
            marcxml_response=make_response(status_code, content),
        )

    def test_valid_export(self):
        resource = self._resource(VALID_MARCXML)
        self.assertTrue(resource.has_marcxml)
        self.assertEqual(resource.marcxml, VALID_MARCXML.decode("utf-8"))
        self.assertIn("José Muñoz", resource.marcxml)
        self.assertEqual(resource.marcxml_error, "")
        self.assertEqual(resource.error, "")

    def test_export_not_fetched(self):
        resource = Resource(123, data={"title": "Papers"})
        self.assertFalse(resource.has_marcxml)
        self.assertEqual(resource.marcxml, "")
        self.assertEqual(resource.marcxml_error, "")
        self.assertEqual(resource.error, "")

    def test_record_as_root_is_accepted(self):
        content = b'<record xmlns="http://www.loc.gov/MARC21/slim"><leader/></record>'
        resource = self._resource(content)
        self.assertTrue(resource.has_marcxml)
        self.assertEqual(resource.marcxml, content.decode("utf-8"))

    def test_http_error_includes_status_and_body(self):
        resource = self._resource(b'{"error":"Resource not found"}', 404)
        self.assertFalse(resource.has_marcxml)
        self.assertEqual(resource.marcxml, "")
        self.assertIn("HTTP 404", resource.marcxml_error)
        self.assertIn("Resource not found", resource.marcxml_error)

    def test_long_error_body_is_truncated(self):
        resource = self._resource(b"x" * 2000, 500)
        self.assertLess(len(resource.marcxml_error), 600)

    def test_not_well_formed_xml(self):
        resource = self._resource(VALID_MARCXML.replace(b"</record>", b""))
        self.assertFalse(resource.has_marcxml)
        self.assertIn("not well-formed XML", resource.marcxml_error)

    def test_not_utf8(self):
        content = (
            '<?xml version="1.0" encoding="ISO-8859-1"?>'
            '<collection xmlns="http://www.loc.gov/MARC21/slim">'
            "<record><leader/>José</record></collection>"
        ).encode("iso-8859-1")
        resource = self._resource(content)
        self.assertFalse(resource.has_marcxml)
        self.assertIn("not valid UTF-8", resource.marcxml_error)

    def test_wrong_root_element(self):
        resource = self._resource(b"<ead><record/></ead>")
        self.assertIn("not a MARCXML collection or record", resource.marcxml_error)

    def test_root_without_marc_namespace(self):
        resource = self._resource(b"<collection><record/></collection>")
        self.assertIn("not a MARCXML collection or record", resource.marcxml_error)

    def test_collection_with_no_records(self):
        resource = self._resource(
            b'<collection xmlns="http://www.loc.gov/MARC21/slim"/>'
        )
        self.assertIn("expected 1 MARC record, found 0", resource.marcxml_error)

    def test_collection_with_two_records(self):
        content = (
            b'<collection xmlns="http://www.loc.gov/MARC21/slim">'
            b"<record/><record/></collection>"
        )
        resource = self._resource(content)
        self.assertIn("expected 1 MARC record, found 2", resource.marcxml_error)


class TestResourceError(unittest.TestCase):
    """Tests for Resource.error, which reports the first problem found."""

    def test_missing_record(self):
        resource = Resource(99)
        self.assertIn("Resource record not available", resource.error)
        self.assertFalse(resource.has_marcxml)

    def test_export_problem(self):
        resource = Resource(
            1, data={"title": "Papers"}, marcxml_response=make_response(500, b"Oops")
        )
        self.assertEqual(resource.error, resource.marcxml_error)
        self.assertIn("HTTP 500", resource.error)


if __name__ == "__main__":
    unittest.main()
