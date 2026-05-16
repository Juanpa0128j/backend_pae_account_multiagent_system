import logging
from app.workers.hatchet_client import get_hatchet
from app.workers.accounting_workflow import accounting_workflow
from app.workers.ingest_workflow import ingest_workflow

logger = logging.getLogger(__name__)


def main() -> None:
    hatchet = get_hatchet()
    worker = hatchet.worker(
        "pae-worker",
        workflows=[accounting_workflow, ingest_workflow],
    )
    logger.info("Starting PAE Hatchet worker...")
    worker.start()


if __name__ == "__main__":
    main()
