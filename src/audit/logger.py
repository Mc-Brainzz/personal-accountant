"""
Audit Logger

DESIGN DECISION: Every significant action in the system is logged.
This provides:
1. Complete traceability
2. Debugging capability
3. User can see history of their interactions
4. Compliance readiness

The audit logger:
- Is async to not block main flow
- Gracefully handles failures (doesn't crash the app if logging fails)
- Supports correlation IDs to trace related events
"""

import asyncio
from typing import Optional
from uuid import UUID, uuid4

import structlog

from src.models.audit import AuditEvent, AuditEventBuilder
from src.services.storage import AuditStorageInterface


# Configure structlog for local logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer()
    ],
    wrapper_class=structlog.stdlib.BoundLogger,
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)


class AuditLogger:
    """
    Central audit logging service.
    
    Logs events both to:
    1. Structured local log (for debugging)
    2. Google Sheets (for persistence and user visibility)
    """
    
    def __init__(
        self,
        storage: Optional[AuditStorageInterface] = None,
    ):
        """
        Initialize audit logger.
        
        Args:
            storage: Storage backend for persistence.
                    If None, only logs locally.
        """
        self._storage = storage
        self._logger = structlog.get_logger()
    
    async def log(self, event: AuditEvent) -> bool:
        """
        Log an audit event.
        
        Always logs locally. Persists to storage if available.
        
        Returns True if storage write succeeded (or no storage configured).
        """
        # Always log locally
        log_dict = event.to_log_dict()
        
        if event.severity.value == "error":
            self._logger.error("audit_event", **log_dict)
        elif event.severity.value == "warning":
            self._logger.warning("audit_event", **log_dict)
        else:
            self._logger.info("audit_event", **log_dict)
        
        # Persist to storage if available
        if self._storage:
            try:
                return await self._storage.append_event(event)
            except Exception as e:
                # Log failure but don't raise
                self._logger.error(
                    "audit_storage_failed",
                    error=str(e),
                    event_id=str(event.event_id),
                )
                return False
        
        return True
    
    async def log_image_uploaded(
        self,
        upload_id: UUID,
        filename: str,
        file_size: int,
        correlation_id: UUID,
    ) -> None:
        """Log image upload event."""
        event = AuditEventBuilder.image_uploaded(
            upload_id=upload_id,
            filename=filename,
            file_size=file_size,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_image_enhanced(
        self,
        upload_id: UUID,
        quality: str,
        enhancements: list[str],
        correlation_id: UUID,
    ) -> None:
        """Log image enhancement completion."""
        event = AuditEventBuilder.image_enhancement_completed(
            upload_id=upload_id,
            quality=quality,
            enhancements=enhancements,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_image_quality_failed(
        self,
        upload_id: UUID,
        quality: str,
        issues: list[str],
        correlation_id: UUID,
    ) -> None:
        """Log image quality check failure."""
        event = AuditEventBuilder.image_quality_failed(
            upload_id=upload_id,
            quality=quality,
            issues=issues,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_ocr_completed(
        self,
        extraction_id: UUID,
        document_type: str,
        confidence: float,
        correlation_id: UUID,
    ) -> None:
        """Log OCR completion."""
        event = AuditEventBuilder.ocr_completed(
            extraction_id=extraction_id,
            document_type=document_type,
            confidence=confidence,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_document_rejected(
        self,
        extraction_id: UUID,
        detected_type: str,
        correlation_id: UUID,
    ) -> None:
        """Log document type rejection."""
        event = AuditEventBuilder.document_type_rejected(
            extraction_id=extraction_id,
            detected_type=detected_type,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_validation_failed(
        self,
        extraction_id: UUID,
        stage: str,
        issues: list[dict],
        correlation_id: UUID,
    ) -> None:
        """Log validation failure."""
        event = AuditEventBuilder.validation_failed(
            extraction_id=extraction_id,
            stage=stage,
            issues=issues,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_user_confirmed(
        self,
        bill_id: UUID,
        extraction_id: UUID,
        correlation_id: UUID,
    ) -> None:
        """Log user confirmation."""
        event = AuditEventBuilder.user_confirmed(
            bill_id=bill_id,
            extraction_id=extraction_id,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_user_rejected(
        self,
        extraction_id: UUID,
        reason: Optional[str],
        correlation_id: UUID,
    ) -> None:
        """Log user rejection."""
        event = AuditEventBuilder.user_rejected(
            extraction_id=extraction_id,
            reason=reason,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_bill_saved(
        self,
        bill_id: UUID,
        vendor: str,
        amount: str,
        correlation_id: UUID,
    ) -> None:
        """Log bill save."""
        event = AuditEventBuilder.bill_saved(
            bill_id=bill_id,
            vendor=vendor,
            amount=amount,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_query_executed(
        self,
        query_id: UUID,
        query_type: str,
        result_count: int,
        correlation_id: UUID,
    ) -> None:
        """Log query execution."""
        event = AuditEventBuilder.query_executed(
            query_id=query_id,
            query_type=query_type,
            result_count=result_count,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_error(
        self,
        error_type: str,
        error_message: str,
        details: Optional[dict] = None,
        correlation_id: Optional[UUID] = None,
    ) -> None:
        """Log an error."""
        event = AuditEventBuilder.system_error(
            error_type=error_type,
            error_message=error_message,
            details=details,
            correlation_id=correlation_id,
        )
        await self.log(event)
    
    async def log_external_service_error(
        self,
        service: str,
        error_message: str,
        correlation_id: UUID,
    ) -> None:
        """Log external service error."""
        event = AuditEventBuilder.external_service_error(
            service=service,
            error_message=error_message,
            correlation_id=correlation_id,
        )
        await self.log(event)


def create_correlation_id() -> UUID:
    """
    Create a new correlation ID for tracking related events.
    
    Use this at the start of a new user action (e.g., bill upload).
    Pass it through all subsequent operations.
    """
    return uuid4()
