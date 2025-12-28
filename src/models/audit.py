"""
Audit Models for Personal Accountant

Every significant action in the system is logged for audit purposes.
This provides:
1. Complete traceability of all operations
2. Debugging information when things go wrong
3. Compliance and accountability
4. Ability to reconstruct history

DESIGN DECISION: Audit logs are append-only. We never delete or modify them.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class AuditEventType(str, Enum):
    """
    Types of events we audit.
    
    Every step in the bill processing pipeline has its own event type.
    """
    # Image processing
    IMAGE_UPLOADED = "image_uploaded"
    IMAGE_ENHANCEMENT_STARTED = "image_enhancement_started"
    IMAGE_ENHANCEMENT_COMPLETED = "image_enhancement_completed"
    IMAGE_ENHANCEMENT_FAILED = "image_enhancement_failed"
    IMAGE_QUALITY_CHECK_PASSED = "image_quality_check_passed"
    IMAGE_QUALITY_CHECK_FAILED = "image_quality_check_failed"
    
    # OCR processing
    OCR_STARTED = "ocr_started"
    OCR_COMPLETED = "ocr_completed"
    OCR_FAILED = "ocr_failed"
    DOCUMENT_TYPE_REJECTED = "document_type_rejected"
    
    # Validation
    SCHEMA_VALIDATION_PASSED = "schema_validation_passed"
    SCHEMA_VALIDATION_FAILED = "schema_validation_failed"
    SEMANTIC_VALIDATION_PASSED = "semantic_validation_passed"
    SEMANTIC_VALIDATION_FAILED = "semantic_validation_failed"
    
    # Human confirmation
    REVIEW_PRESENTED_TO_USER = "review_presented_to_user"
    USER_CONFIRMED = "user_confirmed"
    USER_REJECTED = "user_rejected"
    USER_EDITED = "user_edited"
    
    # Persistence
    BILL_SAVED = "bill_saved"
    BILL_UPDATED = "bill_updated"
    BILL_DELETED = "bill_deleted"
    SAVE_FAILED = "save_failed"
    
    # Query operations
    QUERY_RECEIVED = "query_received"
    QUERY_STRUCTURED = "query_structured"
    QUERY_EXECUTED = "query_executed"
    QUERY_FAILED = "query_failed"
    RESPONSE_GENERATED = "response_generated"
    
    # Payment status
    PAYMENT_STATUS_UPDATED = "payment_status_updated"
    
    # System events
    SYSTEM_ERROR = "system_error"
    EXTERNAL_SERVICE_ERROR = "external_service_error"


class AuditSeverity(str, Enum):
    """Severity level for audit events."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AuditEvent(BaseModel):
    """
    A single audit event.
    
    This is the core unit of our audit trail.
    Every significant action creates one of these.
    """
    
    # Identity
    event_id: UUID = Field(
        default_factory=uuid4,
        description="Unique event identifier"
    )
    timestamp: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the event occurred (UTC)"
    )
    
    # Event classification
    event_type: AuditEventType = Field(
        ...,
        description="Type of event"
    )
    severity: AuditSeverity = Field(
        default=AuditSeverity.INFO,
        description="Event severity"
    )
    
    # Context - what entity is this about?
    entity_type: Optional[str] = Field(
        default=None,
        description="Type of entity (e.g., 'bill', 'image', 'query')"
    )
    entity_id: Optional[UUID] = Field(
        default=None,
        description="ID of the entity this event relates to"
    )
    
    # Correlation - for tracking related events
    correlation_id: Optional[UUID] = Field(
        default=None,
        description="ID to correlate related events (e.g., all events in one bill upload)"
    )
    
    # Event details
    description: str = Field(
        ...,
        max_length=500,
        description="Human-readable description of what happened"
    )
    
    # Additional data (event-specific)
    details: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional event-specific data"
    )
    
    # Error information (if applicable)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    stack_trace: Optional[str] = None
    
    # User action tracking
    is_user_action: bool = Field(
        default=False,
        description="Was this triggered by a user action?"
    )
    
    def to_log_dict(self) -> dict:
        """
        Convert to a dictionary suitable for structured logging.
        """
        return {
            "event_id": str(self.event_id),
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type.value,
            "severity": self.severity.value,
            "entity_type": self.entity_type,
            "entity_id": str(self.entity_id) if self.entity_id else None,
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
            "description": self.description,
            "details": self.details,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "is_user_action": self.is_user_action,
        }
    
    def to_sheets_row(self) -> list:
        """
        Convert to a row suitable for Google Sheets storage.
        
        Returns columns in order:
        [event_id, timestamp, event_type, severity, entity_type, entity_id,
         correlation_id, description, details_json, error_message, is_user_action]
        """
        import json
        
        return [
            str(self.event_id),
            self.timestamp.isoformat(),
            self.event_type.value,
            self.severity.value,
            self.entity_type or "",
            str(self.entity_id) if self.entity_id else "",
            str(self.correlation_id) if self.correlation_id else "",
            self.description,
            json.dumps(self.details) if self.details else "",
            self.error_message or "",
            str(self.is_user_action),
        ]


class AuditEventBuilder:
    """
    Helper class to build audit events with common patterns.
    
    Usage:
        event = AuditEventBuilder.image_uploaded(upload_id, filename)
        event = AuditEventBuilder.user_confirmed(bill_id, correlation_id)
    """
    
    @staticmethod
    def image_uploaded(
        upload_id: UUID,
        filename: str,
        file_size: int,
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.IMAGE_UPLOADED,
            entity_type="image",
            entity_id=upload_id,
            correlation_id=correlation_id,
            description=f"Image uploaded: {filename}",
            details={
                "filename": filename,
                "file_size_bytes": file_size,
            },
            is_user_action=True,
        )
    
    @staticmethod
    def image_enhancement_completed(
        upload_id: UUID,
        quality: str,
        enhancements: list[str],
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.IMAGE_ENHANCEMENT_COMPLETED,
            entity_type="image",
            entity_id=upload_id,
            correlation_id=correlation_id,
            description=f"Image enhanced with quality: {quality}",
            details={
                "quality_assessment": quality,
                "enhancements_applied": enhancements,
            },
        )
    
    @staticmethod
    def image_quality_failed(
        upload_id: UUID,
        quality: str,
        issues: list[str],
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.IMAGE_QUALITY_CHECK_FAILED,
            severity=AuditSeverity.WARNING,
            entity_type="image",
            entity_id=upload_id,
            correlation_id=correlation_id,
            description=f"Image quality check failed: {quality}",
            details={
                "quality_assessment": quality,
                "issues": issues,
            },
        )
    
    @staticmethod
    def ocr_completed(
        extraction_id: UUID,
        document_type: str,
        confidence: float,
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.OCR_COMPLETED,
            entity_type="extraction",
            entity_id=extraction_id,
            correlation_id=correlation_id,
            description=f"OCR completed with {confidence:.0%} confidence",
            details={
                "document_type": document_type,
                "confidence_score": confidence,
            },
        )
    
    @staticmethod
    def document_type_rejected(
        extraction_id: UUID,
        detected_type: str,
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.DOCUMENT_TYPE_REJECTED,
            severity=AuditSeverity.WARNING,
            entity_type="extraction",
            entity_id=extraction_id,
            correlation_id=correlation_id,
            description=f"Document rejected: not a valid bill/invoice (detected: {detected_type})",
            details={
                "detected_type": detected_type,
            },
        )
    
    @staticmethod
    def validation_failed(
        extraction_id: UUID,
        stage: str,
        issues: list[dict],
        correlation_id: UUID
    ) -> AuditEvent:
        event_type = (
            AuditEventType.SCHEMA_VALIDATION_FAILED
            if stage == "schema"
            else AuditEventType.SEMANTIC_VALIDATION_FAILED
        )
        return AuditEvent(
            event_type=event_type,
            severity=AuditSeverity.WARNING,
            entity_type="extraction",
            entity_id=extraction_id,
            correlation_id=correlation_id,
            description=f"{stage.capitalize()} validation failed with {len(issues)} issues",
            details={
                "stage": stage,
                "issues": issues,
            },
        )
    
    @staticmethod
    def user_confirmed(
        bill_id: UUID,
        extraction_id: UUID,
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.USER_CONFIRMED,
            entity_type="bill",
            entity_id=bill_id,
            correlation_id=correlation_id,
            description="User confirmed extracted bill data",
            details={
                "extraction_id": str(extraction_id),
            },
            is_user_action=True,
        )
    
    @staticmethod
    def user_rejected(
        extraction_id: UUID,
        reason: Optional[str],
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.USER_REJECTED,
            entity_type="extraction",
            entity_id=extraction_id,
            correlation_id=correlation_id,
            description="User rejected extracted bill data",
            details={
                "reason": reason or "No reason provided",
            },
            is_user_action=True,
        )
    
    @staticmethod
    def bill_saved(
        bill_id: UUID,
        vendor: str,
        amount: str,
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.BILL_SAVED,
            entity_type="bill",
            entity_id=bill_id,
            correlation_id=correlation_id,
            description=f"Bill saved: {vendor} - ₹{amount}",
            details={
                "vendor": vendor,
                "amount": amount,
            },
        )
    
    @staticmethod
    def query_executed(
        query_id: UUID,
        query_type: str,
        result_count: int,
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.QUERY_EXECUTED,
            entity_type="query",
            entity_id=query_id,
            correlation_id=correlation_id,
            description=f"Query executed: {query_type} returned {result_count} results",
            details={
                "query_type": query_type,
                "result_count": result_count,
            },
        )
    
    @staticmethod
    def system_error(
        error_type: str,
        error_message: str,
        details: Optional[dict] = None,
        correlation_id: Optional[UUID] = None
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.SYSTEM_ERROR,
            severity=AuditSeverity.ERROR,
            description=f"System error: {error_type}",
            error_message=error_message,
            details=details or {},
            correlation_id=correlation_id,
        )
    
    @staticmethod
    def external_service_error(
        service: str,
        error_message: str,
        correlation_id: UUID
    ) -> AuditEvent:
        return AuditEvent(
            event_type=AuditEventType.EXTERNAL_SERVICE_ERROR,
            severity=AuditSeverity.ERROR,
            description=f"External service error: {service}",
            error_message=error_message,
            details={
                "service": service,
            },
            correlation_id=correlation_id,
        )
