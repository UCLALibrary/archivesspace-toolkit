import unittest
from asnake.client import ASnakeClient
from MySQLdb import connect
from add_alma_barcodes_to_archivesspace import (
    _get_container_refs_from_api,
    _get_container_refs_from_db,
)


class TestContainerRetrieval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Assumes config file with local information
        cls.aspace_client = ASnakeClient(config_file=".archivessnake_secret_DEV.yml")
        db_settings = cls.aspace_client.config.get("database")
        cls.mysql_client = connect(
            host=db_settings.get("host"),
            database=db_settings.get("database"),
            user=db_settings.get("user"),
            password=db_settings.get("password"),
        )

    @classmethod
    def tearDownClass(cls):
        cls.mysql_client.close()

    def test_retrievals_are_identical(self):
        resource_id = 3002  # should have just a few top containers
        api_refs = _get_container_refs_from_api(self.aspace_client, resource_id)
        db_refs = _get_container_refs_from_db(self.mysql_client, resource_id)
        # Don't check sizes as local data can change, but confirm the values are the same.
        self.assertEqual(api_refs, db_refs)
