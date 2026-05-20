import argparse
import asnake.logging as logging

from asnake.client import ASnakeClient
from pathlib import Path
from utils import configure_logging

# Logger available globally within this module.
# Configuration is done by configure_logging(), which is called by main().
# Made available globally so that tests can use the same logger with their own configuration.
logger = logging.get_logger(Path(__file__).stem)

client = ASnakeClient()


def _get_args() -> argparse.Namespace:
    """Returns the command-line arguments for this program.

    :return: Parsed CLI arguments.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--page_size",
        help="Number of records to retrieve per page",
        default=250,
        type=int,
    )
    return parser.parse_args()


def get_unlinked_top_containers(page_size: int = 250):
    """Retrieves all unlinked top containers from an ASpace repository and writes them to a file.

    :param int page_size: Number of records to retrieve per page.
    """

    output_list = []
    for top_container in client.get_paged(
        "repositories/2/top_containers", page_size=page_size
    ):
        # unlinked top containers have an empty collection field
        if len(top_container["collection"]) == 0:
            logger.info(f"Unlinked top container: {top_container['uri']}")
            output_list.append(top_container["uri"])

    logger.info(f"Total unlinked top containers: {len(output_list)}")
    with open("unlinked_top_containers.txt", "w") as f:
        for item in output_list:
            f.write(f"{item}\n")
    logger.info("Output written to unlinked_top_containers.txt")


def main() -> None:
    configure_logging(Path(__file__).stem)
    args = _get_args()
    get_unlinked_top_containers(args.page_size)


if __name__ == "__main__":
    main()
