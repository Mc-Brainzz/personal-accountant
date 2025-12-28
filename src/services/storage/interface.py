"""
Abstract Storage Interface

DESIGN DECISION: We define an abstract interface for storage operations.
This allows us to:
1. Swap Google Sheets for a real database later
2. Use in-memory storage for testing
3. Add caching layers transparently
4. Keep business logic decoupled from storage implementation

The interface is intentionally simple - we're not building a full ORM.
Just the operations we need for bill management.
"""

from abc import ABC, abstractmethod
from datetime import date
from typing import Optional
from uuid import UUID

from src.models.bill import (
    BillCategory,
    ConfirmedBill,
    PaymentStatus,
)
from src.models.audit import AuditEvent


class BillStorageInterface(ABC):
    """
    Abstract interface for bill storage operations.
    
    Any storage implementation (Google Sheets, PostgreSQL, etc.)
    must implement these methods.
    """
    
    @abstractmethod
    async def save_bill(self, bill: ConfirmedBill) -> bool:
        """
        Save a confirmed bill to storage.
        
        Args:
            bill: The confirmed bill to save
            
        Returns:
            True if saved successfully
            
        Raises:
            StorageError: If save fails
        """
        pass
    
    @abstractmethod
    async def get_bill_by_id(self, bill_id: UUID) -> Optional[ConfirmedBill]:
        """
        Retrieve a bill by its ID.
        
        Args:
            bill_id: The bill's unique identifier
            
        Returns:
            The bill if found, None otherwise
        """
        pass
    
    @abstractmethod
    async def update_bill(self, bill: ConfirmedBill) -> bool:
        """
        Update an existing bill.
        
        Args:
            bill: The bill with updated fields
            
        Returns:
            True if updated successfully
            
        Raises:
            StorageError: If update fails
            NotFoundError: If bill doesn't exist
        """
        pass
    
    @abstractmethod
    async def delete_bill(self, bill_id: UUID) -> bool:
        """
        Delete a bill by ID.
        
        Args:
            bill_id: The bill's unique identifier
            
        Returns:
            True if deleted successfully
        """
        pass
    
    @abstractmethod
    async def list_bills(
        self,
        category: Optional[BillCategory] = None,
        vendor: Optional[str] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        payment_status: Optional[PaymentStatus] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[ConfirmedBill]:
        """
        List bills with optional filters.
        
        Args:
            category: Filter by category
            vendor: Filter by vendor name (partial match)
            date_from: Filter bills on or after this date
            date_to: Filter bills on or before this date
            payment_status: Filter by payment status
            limit: Maximum number of results
            offset: Number of results to skip
            
        Returns:
            List of matching bills
        """
        pass
    
    @abstractmethod
    async def get_total_by_category(
        self,
        category: BillCategory,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> float:
        """
        Get total amount for a category in a date range.
        
        Args:
            category: Bill category to sum
            date_from: Start of date range
            date_to: End of date range
            
        Returns:
            Total amount in INR
        """
        pass
    
    @abstractmethod
    async def get_total_by_vendor(
        self,
        vendor: str,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> float:
        """
        Get total amount for a vendor in a date range.
        
        Args:
            vendor: Vendor name
            date_from: Start of date range
            date_to: End of date range
            
        Returns:
            Total amount in INR
        """
        pass
    
    @abstractmethod
    async def bill_exists(
        self,
        vendor: str,
        bill_number: Optional[str],
        bill_date: date,
    ) -> bool:
        """
        Check if a similar bill already exists (duplicate detection).
        
        Args:
            vendor: Vendor name
            bill_number: Bill number if available
            bill_date: Date on the bill
            
        Returns:
            True if a matching bill exists
        """
        pass


class AuditStorageInterface(ABC):
    """
    Abstract interface for audit log storage.
    
    Audit logs are append-only - we never delete or modify them.
    """
    
    @abstractmethod
    async def append_event(self, event: AuditEvent) -> bool:
        """
        Append an audit event to the log.
        
        Args:
            event: The audit event to log
            
        Returns:
            True if logged successfully
        """
        pass
    
    @abstractmethod
    async def get_events_by_correlation_id(
        self,
        correlation_id: UUID,
    ) -> list[AuditEvent]:
        """
        Get all events for a correlation ID (e.g., one bill upload flow).
        
        Args:
            correlation_id: The correlation identifier
            
        Returns:
            List of related events in chronological order
        """
        pass
    
    @abstractmethod
    async def get_events_by_entity(
        self,
        entity_type: str,
        entity_id: UUID,
    ) -> list[AuditEvent]:
        """
        Get all events for a specific entity.
        
        Args:
            entity_type: Type of entity (e.g., 'bill', 'image')
            entity_id: The entity's ID
            
        Returns:
            List of events in chronological order
        """
        pass
    
    @abstractmethod
    async def get_recent_events(
        self,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """
        Get the most recent audit events.
        
        Args:
            limit: Maximum number of events to return
            
        Returns:
            List of recent events (newest first)
        """
        pass


class StorageError(Exception):
    """Base exception for storage operations."""
    pass


class NotFoundError(StorageError):
    """Entity not found in storage."""
    pass


class DuplicateError(StorageError):
    """Attempted to insert a duplicate entity."""
    pass


class ConnectionError(StorageError):
    """Could not connect to storage backend."""
    pass
