import unittest
from unittest.mock import MagicMock

from tests.test_aspace_models import VALID_MARCXML, make_response
from utils.aspace_models import OCLC_SOURCE
from utils.aspace_utils import (
    get_resource,
    get_resources,
)


def _make_resource_data(resource_id: int, repo_id: int = 2, **overrides) -> dict:
    """Return a minimal ASpace resource dict, with optional overrides."""
    data = {
        "uri": f"/repositories/{repo_id}/resources/{resource_id}",
        "title": f"Collection {resource_id}",
        "id_0": f"MSS-{resource_id}",
        "external_ids": [{"source": OCLC_SOURCE, "external_id": f"ocn{resource_id}"}],
    }
    data.update(overrides)
    return data


def _make_client(resources: dict, exports: dict) -> MagicMock:
    """Return a mock ASnakeClient.

    :param dict resources: resource URI -> resource dict. URIs not listed return 404.
    :param dict exports: MARC export path -> (status code, content). Paths not listed
        return 404.
    """

    def get(path, params=None):
        if path in resources:
            return make_response(200, json_data=resources[path])
        if path in exports:
            return make_response(*exports[path])
        return make_response(404, b'{"error":"Not found"}')

    client = MagicMock()
    client.get.side_effect = get
    return client


class TestGetResource(unittest.TestCase):
    """Tests for get_resource()."""

    def test_record_only_by_default(self):
        client = _make_client(
            resources={"/repositories/2/resources/1": _make_resource_data(1)},
            exports={},
        )
        resource = get_resource(client, 1)
        self.assertTrue(resource.found)
        self.assertEqual(resource.identifier, "MSS-1")
        self.assertEqual(resource.oclc_ids, ["ocn1"])
        self.assertIsNone(resource.marcxml_response)
        client.get.assert_called_once_with("/repositories/2/resources/1")

    def test_with_marcxml(self):
        client = _make_client(
            resources={"/repositories/2/resources/1": _make_resource_data(1)},
            exports={"/repositories/2/resources/marc21/1.xml": (200, VALID_MARCXML)},
        )
        resource = get_resource(client, 1, include_marcxml=True)
        self.assertTrue(resource.has_marcxml)
        self.assertEqual(resource.marcxml, VALID_MARCXML.decode("utf-8"))
        self.assertEqual(resource.error, "")
        client.get.assert_called_with(
            "/repositories/2/resources/marc21/1.xml",
            params={"include_unpublished_marc": "false"},
        )

    def test_include_unpublished(self):
        client = _make_client(
            resources={"/repositories/2/resources/1": _make_resource_data(1)},
            exports={"/repositories/2/resources/marc21/1.xml": (200, VALID_MARCXML)},
        )
        get_resource(client, 1, include_marcxml=True, include_unpublished=True)
        client.get.assert_called_with(
            "/repositories/2/resources/marc21/1.xml",
            params={"include_unpublished_marc": "true"},
        )

    def test_missing_record_skips_export(self):
        client = _make_client(resources={}, exports={})
        resource = get_resource(client, 99, include_marcxml=True)
        self.assertFalse(resource.found)
        self.assertEqual(resource.uri, "/repositories/2/resources/99")
        self.assertIn("Resource record not available", resource.error)
        # Only the record was requested, not the MARC export.
        client.get.assert_called_once_with("/repositories/2/resources/99")

    def test_export_failure_keeps_record(self):
        client = _make_client(
            resources={"/repositories/2/resources/1": _make_resource_data(1)},
            exports={"/repositories/2/resources/marc21/1.xml": (500, b"Server error")},
        )
        resource = get_resource(client, 1, include_marcxml=True)
        self.assertFalse(resource.has_marcxml)
        self.assertIn("HTTP 500", resource.error)
        self.assertEqual(resource.identifier, "MSS-1")
        self.assertEqual(resource.oclc_ids, ["ocn1"])

    def test_repo_id_used_in_requests(self):
        client = _make_client(
            resources={
                "/repositories/5/resources/1": _make_resource_data(1, repo_id=5)
            },
            exports={"/repositories/5/resources/marc21/1.xml": (200, VALID_MARCXML)},
        )
        resource = get_resource(client, 1, repo_id=5, include_marcxml=True)
        self.assertEqual(resource.uri, "/repositories/5/resources/1")
        self.assertTrue(resource.has_marcxml)


class TestGetResources(unittest.TestCase):
    """Tests for get_resources()."""

    def test_failures_do_not_stop_batch_and_order_is_kept(self):
        client = _make_client(
            resources={
                "/repositories/2/resources/1": _make_resource_data(1),
                "/repositories/2/resources/3": _make_resource_data(3, external_ids=[]),
            },
            exports={
                "/repositories/2/resources/marc21/1.xml": (200, b"<not-marc/>"),
                "/repositories/2/resources/marc21/3.xml": (200, VALID_MARCXML),
            },
        )
        resources = get_resources(client, [1, 2, 3], include_marcxml=True)
        self.assertEqual([r.resource_id for r in resources], [1, 2, 3])
        self.assertIn("malformed", resources[0].error)
        self.assertIn("Resource record not available", resources[1].error)
        self.assertEqual(resources[2].error, "")
        self.assertEqual(resources[2].oclc_ids, [])

    def test_empty_list(self):
        client = _make_client(resources={}, exports={})
        self.assertEqual(get_resources(client, []), [])
        client.get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
