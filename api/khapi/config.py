"""Application settings, read from environment variables.

Locally the variables come from a ``.env`` file (python-dotenv); on Azure
App Service they are App Settings. Secrets are never hard-coded: the API
only stores SHA-256 hashes of the accepted API keys.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

VALID_BACKENDS = ("memory", "sqlite", "azuresql")


def _split_hashes(raw: str) -> tuple[str, ...]:
    """Split a comma-separated list of hex digests into a normalised tuple."""
    return tuple(part.strip().lower() for part in raw.split(",") if part.strip())


@dataclass(frozen=True)
class Settings:
    """All runtime settings of the API.

    Attributes:
        api_key_hashes: SHA-256 hex digests of the keys that may call the
            protected endpoints. Two values allow key rotation without
            downtime.
        storage_backend: ``memory`` (tests), ``sqlite`` (local, Task 3) or
            ``azuresql`` (Azure SQL Database, Task 4).
        sqlite_path: Location of the local SQLite file.
        sql_connection_string: ODBC connection string for Azure SQL.
        max_batch_size: Most measurements accepted in one POST.
        max_content_length: Largest request body in bytes.
        max_age_days: Oldest measurement timestamp accepted.
        log_level: Python logging level name.
        log_file: Optional path for a rotating log file (local use).
        proxy_hops: Reverse proxies in front of the app whose
            X-Forwarded-* headers are trusted: 1 on App Service, 0 on the
            on-prem collector (gunicorn faces the clients directly, so a
            client could otherwise fake its address in the log).
    """

    api_key_hashes: tuple[str, ...]
    storage_backend: str = "sqlite"
    sqlite_path: str = "instance/khapi.db"
    sql_connection_string: str = ""
    max_batch_size: int = 500
    max_content_length: int = 256 * 1024
    max_age_days: int = 30
    log_level: str = "INFO"
    log_file: str = ""
    proxy_hops: int = 1

    def __post_init__(self) -> None:
        if self.storage_backend not in VALID_BACKENDS:
            raise ValueError(
                f"KH_STORAGE_BACKEND must be one of {VALID_BACKENDS}, "
                f"got {self.storage_backend!r}"
            )

    @classmethod
    def from_env(cls) -> "Settings":
        """Build settings from the environment (loading ``.env`` first)."""
        load_dotenv()
        return cls(
            api_key_hashes=_split_hashes(os.getenv("KH_API_KEY_HASHES", "")),
            storage_backend=os.getenv("KH_STORAGE_BACKEND", "sqlite").lower(),
            sqlite_path=os.getenv("KH_SQLITE_PATH", "instance/khapi.db"),
            sql_connection_string=os.getenv("KH_SQL_CONNECTION_STRING", ""),
            max_batch_size=int(os.getenv("KH_MAX_BATCH_SIZE", "500")),
            max_content_length=int(
                os.getenv("KH_MAX_CONTENT_LENGTH", str(256 * 1024))
            ),
            max_age_days=int(os.getenv("KH_MAX_AGE_DAYS", "30")),
            log_level=os.getenv("KH_LOG_LEVEL", "INFO").upper(),
            log_file=os.getenv("KH_LOG_FILE", ""),
            proxy_hops=int(os.getenv("KH_PROXY_HOPS", "1")),
        )
