import asnake.logging as logging
from asnake.client import ASnakeClient
import argparse

logging.setup_logging(filename="archivessnake.log", level="INFO")
# set label for custom logger - all output will be in archivessnake.log
logger = logging.get_logger("get_unlinked_top_containers")
client = ASnakeClient()


def get_unlinked_top_containers(page_size: int = 250):

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--page_size", help="Number of records to retrieve per page", default=250
    )
    args = parser.parse_args()

    get_unlinked_top_containers(int(args.page_size))
