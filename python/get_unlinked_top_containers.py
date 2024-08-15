import asnake.logging as logging
from asnake.client import ASnakeClient

logging.setup_logging(filename="archivessnake.log", level="INFO")
# set label for custom logger - all output will be in archivessnake.log
logger = logging.get_logger("get_unlinked_top_containers")
client = ASnakeClient()


def get_unlinked_top_containers():
    all_top_containers = client.get(
        "repositories/2/top_containers", params={"all_ids": True}
    ).json()
    total_top_containers = len(all_top_containers)
    logger.info(f"Total top containers: {total_top_containers}")
    pages = (total_top_containers // 1000) + 1
    logger.info(f"Total pages: {pages}")

    output_list = []
    for i in range(1, pages + 1):
        logger.info(f"Page: {i}")
        top_containers = client.get(
            "repositories/2/top_containers", params={"page": i, "page_size": 1000}
        ).json()
        for top_container in top_containers["results"]:
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
    get_unlinked_top_containers()
