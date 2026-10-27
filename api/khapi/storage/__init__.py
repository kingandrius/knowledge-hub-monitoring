"""Storage back ends and a factory that picks one from the settings."""
from __future__ import annotations

from ..config import Settings
from .base import InsertResult, MeasurementRepository, MetricSummary
from .memory import MemoryRepository
from .sqlite import SQLiteRepository

__all__ = ["InsertResult", "MeasurementRepository", "MetricSummary",
           "MemoryRepository", "SQLiteRepository", "build_repository"]


def build_repository(settings: Settings) -> MeasurementRepository:
    """Create the repository selected by ``KH_STORAGE_BACKEND``."""
    if settings.storage_backend == "memory":
        return MemoryRepository()
    if settings.storage_backend == "sqlite":
        return SQLiteRepository(settings.sqlite_path)
    from .azuresql import AzureSQLRepository  # needs pyodbc + ODBC Driver 18
    return AzureSQLRepository(settings.sql_connection_string)
