import argparse
import asnake.logging as logging

from asnake.client import ASnakeClient
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
        "-c",
        "--config_file",
        help="Path to a YAML configuration file with ArchivesSpace credentials",
        required=True,
    )
    parser.add_argument(
        "--repo_id",
        help="ArchivesSpace repository ID to target. Defaults to 2.",
        required=False,
        type=int,
        default=2,
    )
    parser.add_argument(
        "-o",
        "--output_file",
        help="Path to a file to write the output to. Defaults to unlinked_top_containers.txt.",
        required=False,
        default="unlinked_top_containers.txt",
    )
    parser.add_argument(
        "--page_size",
        help="Number of records to retrieve per page",
        default=250,
        type=int,
    )
    return parser.parse_args()


def get_unlinked_top_containers(
    client: ASnakeClient, repo_id: int, output_file: str, page_size: int = 250
):
    """Retrieves all unlinked top containers from an ASpace repository and writes them to a file.

    :param ASnakeClient client: ASnake client instance.
    :param int repo_id: ArchivesSpace repository ID to target.
    :param str output_file: Path to a file to write the output to.
    :param int page_size: Number of records to retrieve per page.
    """

    output_list = []
    for top_container in client.get_paged(
        f"repositories/{repo_id}/top_containers", page_size=page_size
    ):
        # unlinked top containers have an empty collection field
        if len(top_container["collection"]) == 0:
            logger.info(f"Unlinked top container: {top_container['uri']}")
            output_list.append(top_container["uri"])

    logger.info(f"Total unlinked top containers: {len(output_list)}")
    with open(output_file, "w") as f:
        for item in output_list:
            f.write(f"{item}\n")
    logger.info(f"Output written to {output_file}")


def main() -> None:
    """Retrieves all unlinked top containers from an ASpace repository and writes them to a file."""
    configure_logging(Path(__file__).stem)
    args = _get_args()
    config = load_config(args.config_file)
    client = ASnakeClient(**config)

    get_unlinked_top_containers(client, args.repo_id, args.output_file, args.page_size)


if __name__ == "__main__":
    main()
