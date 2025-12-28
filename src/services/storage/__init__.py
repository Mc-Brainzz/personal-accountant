"""
Storage Services Package

Provides abstract interfaces and concrete implementations for data storage.
Currently implements Google Sheets as the backend, but designed to be swappable.
"""

from src.services.storage.interface import (
    AuditStorageInterface,
    BillStorageInterface,
    ConnectionError,
    DuplicateError,
    NotFoundError,
    StorageError,
)
from src.services.storage.google_sheets import (
    GoogleSheetsAuditStorage,
    GoogleSheetsBillStorage,
    GoogleSheetsClient,
)

__all__ = [
    # Interfaces
    "AuditStorageInterface",
    "BillStorageInterface",
    # Exceptions
    "ConnectionError",
    "DuplicateError",
    "NotFoundError",
    "StorageError",
    # Google Sheets implementation
    "GoogleSheetsAuditStorage",
    "GoogleSheetsBillStorage",
    "GoogleSheetsClient",
]
