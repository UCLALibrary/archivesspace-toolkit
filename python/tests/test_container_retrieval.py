import unittest
from asnake.client import ASnakeClient
from utils.aspace_utils import (
    get_container_refs_from_api,
    get_container_refs_from_db,
)


class TestContainerRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Assumes config file with local information
        cls.aspace_client = ASnakeClient(config_file=".archivessnake_secret_DEV.yml")
        cls.db_settings = cls.aspace_client.config.get("database")

    def test_retrievals_are_identical(self):
        repo_id = 2
        resource_id = 3002  # should have just a few top containers
        api_refs = get_container_refs_from_api(self.aspace_client, repo_id, resource_id)
        db_refs = get_container_refs_from_db(self.db_settings, resource_id)
        # Don't check sizes as local data can change, but confirm the values are the same.
        self.assertEqual(api_refs, db_refs)
