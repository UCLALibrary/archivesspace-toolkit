import asnake.logging as logging
from asnake.client import ASnakeClient
import argparse

from pathlib import Path

from utils import configure_logging, load_config

# Logger available globally within this module.
# Configuration is done by configure_logging(), which is called by main().
# Made available globally so that tests can use the same logger with their own configuration.
logger = logging.get_logger(Path(__file__).stem)


def _get_args() -> argparse.Namespace:
    """Returns the command-line arguments for this program.

    :return: Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-f",
        "--container_list_file",
        help="Path to a file containing a list of top container URIs to delete",
    )
    parser.add_argument(
        "-c",
        "--config_file",
        help="Path to a YAML configuration file with ArchivesSpace credentials",
        required=True,
    )
    parser.add_argument(
        "--dry_run",
        action="store_true",
        help="Log intended deletions without updating ArchivesSpace.",
    )
    return parser.parse_args()


def container_is_unlinked(client: ASnakeClient, top_container_uri: str) -> bool:
    """Checks if a top container is unlinked by looking foran empty collection field.

    :param str top_container_uri: The URI of the top container to check.
    :return: True if the top container is unlinked, False otherwise.
    """
    top_container = client.get(top_container_uri).json()
    return len(top_container["collection"]) == 0


def container_has_errors(client: ASnakeClient, top_container_uri: str) -> bool:
    """Checks if a top container has errors by looking for an error key in the response.

    :param str top_container_uri: The URI of the top container to check.
    :return: True if the top container has errors, False otherwise.
    """
    top_container = client.get(top_container_uri).json()
    if "error" in top_container.keys():
        logger.error(
            f"Error retrieving top container {top_container_uri}: {top_container['error']}"
        )
        return True
    return False


def delete_unlinked_top_containers(
    client: ASnakeClient, container_list_file: str, dry_run: bool
):
    """Deletes unlinked top containers from an ASpace repository.

    :param ASnakeClient client: ASnake client instance.
    :param str container_list_file: Path to file containing URIs of top containers to delete.
    :param bool dry_run: If True, log intended deletions without updating ArchivesSpace.
    """
    logger.info(f"Reading top container URIs to delete from {container_list_file}")
    with open(container_list_file, "r") as f:
        container_list = f.readlines()
    container_list = [x.strip() for x in container_list]

    deleted_count = 0
    skipped_uri_list = []
    for container in container_list:
        if container_has_errors(client, container):
            # error message already logged in container_has_errors()
            skipped_uri_list.append(container)
            continue
        if container_is_unlinked(client, container):
            if dry_run:
                logger.info(f"DRY RUN: Would delete unlinked top container {container}")
                deleted_count += 1
                continue
            else:
                logger.info(f"Deleting unlinked top container: {container}")
                delete_response = client.delete(container)
                status_code = delete_response.status_code
                if status_code == 200:
                    # All OK
                    deleted_count += 1
                elif status_code == 403:
                    # Forbidden
                    logger.error(
                        f"Permission denied deleting top container {container}:"
                        f"{delete_response.json()}"
                    )
                    raise PermissionError(
                        f"Permission denied deleting top container {container}"
                    )
                else:
                    # Unknown error
                    logger.error(
                        f"Unknown error {status_code} deleting top container {container}:"
                        f"{delete_response.json()}"
                    )
        else:
            logger.info(
                f"Top container {container} is linked to a collection. Skipping deletion."
            )
            skipped_uri_list.append(container)
    logger.info(
        f"{'DRY RUN: Would delete' if dry_run else 'Deleted'} {deleted_count} top containers"
    )
    logger.info(
        f"{'DRY RUN: Would skip' if dry_run else 'Skipped'} "
        f"{len(skipped_uri_list)} top containers: {skipped_uri_list}"
    )


def main() -> None:
    """Deletes unlinked top containers from an ASpace repository."""
    configure_logging(Path(__file__).stem)
    args = _get_args()
    config = load_config(args.config_file)
    client = ASnakeClient(**config)

    delete_unlinked_top_containers(client, args.container_list_file, args.dry_run)


if __name__ == "__main__":
    main()
