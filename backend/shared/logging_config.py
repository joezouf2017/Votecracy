"""Give the application's own log records a handler and a format.

uvicorn configures handlers for its own loggers and leaves the root logger
alone. Application records therefore propagate to a root that has no handler at
all, and Python falls back to `logging.lastResort` — which prints the bare
message to stderr with no level, no timestamp and no logger name, and only at
WARNING or above. Two consequences, and the second is the worse one:

- `log.error("redis unavailable, refusing vote")` reached the container log as
  exactly that string. Nothing marked it as an error, so it cannot be filtered
  for in aggregated logs. The store-divergence message in `daily.persist_vote`
  is documented as the only signal that Redis and Postgres disagree about who
  has voted, and a signal you cannot search for is not much of one.
- `logging.lastResort` is fixed at WARNING, so any `log.info` would have been
  discarded silently. There are none today, which is partly why this went
  unnoticed — the first one added would simply not have appeared.

Deliberately plain text rather than JSON. Phase 6 ships to CloudWatch and can
swap the formatter then; inventing a log schema before there is anything
consuming it would be guessing.
"""

import logging
from logging.config import dictConfig

from shared.settings import get_settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"


def configure_logging() -> None:
    """Attach a formatted stderr handler to the root logger.

    `disable_existing_loggers` stays False so uvicorn's loggers, which are
    configured separately and possibly later, keep working.
    """
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {
                    "format": _FORMAT,
                    "datefmt": "%Y-%m-%dT%H:%M:%S%z",
                }
            },
            "handlers": {
                "stderr": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stderr",
                    "formatter": "standard",
                }
            },
            "root": {
                "handlers": ["stderr"],
                "level": get_settings().log_level.upper(),
            },
        }
    )
    logging.captureWarnings(True)
