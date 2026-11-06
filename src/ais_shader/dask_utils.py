import logging
from typing import Optional

from dask.distributed import Client

logger = logging.getLogger(__name__)


def get_dask_client(scheduler: Optional[str] = None, local_fallback: bool = True) -> Optional[Client]:
    """
    Connect to a Dask scheduler, or start a local cluster when none is given.

    Every pipeline entry point takes the same optional ``--scheduler`` address
    and resolves it the same way, so the connect/fall-back/log sequence lives
    here rather than being repeated per entry point.

    local_fallback : bool
        When False, returning None instead of starting a local cluster if no
        scheduler address was given -- for pipelines whose Dask use is
        optional and that otherwise run in-process.
    """
    if scheduler:
        logger.info(f"Connecting to Dask scheduler at {scheduler}...")
        client = Client(scheduler)
    elif local_fallback:
        logger.info("Starting Local Dask Client...")
        client = Client()
    else:
        return None
    logger.info(f"Dask dashboard: {client.dashboard_link}")
    return client
