import logging
from hatchet_sdk import Hatchet
from hatchet_sdk.config import ClientConfig
from app.core.config import get_settings

logger = logging.getLogger(__name__)

_client: Hatchet | None = None


def get_hatchet() -> Hatchet:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.hatchet_client_token:
            raise RuntimeError(
                "HATCHET_CLIENT_TOKEN must be set when HATCHET_ENABLED=true"
            )
        _client = Hatchet(config=ClientConfig(token=settings.hatchet_client_token))
        logger.info("Hatchet client initialized")
    return _client


def reset_hatchet() -> None:
    """Reset singleton — for use in tests only."""
    global _client
    _client = None
