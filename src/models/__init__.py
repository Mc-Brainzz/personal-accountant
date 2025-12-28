"""
Data Models Package

This package contains all Pydantic models used in the Personal Accountant system.
All data flowing through the system must conform to these schemas.
"""

from src.models.bill import (
    BillCategory,
    BillLineItem,
    BillStatus,
    ConfirmedBill,
    DocumentType,
    EnhancedImage,
    ExtractedBillData,
    ImageQuality,
    ImageUpload,
    PaymentStatus,
    QueryResult,
    StructuredQuery,
    ValidationIssue,
    ValidationResult,
    VendorInfo,
)
from src.models.audit import (
    AuditEvent,
    AuditEventBuilder,
    AuditEventType,
    AuditSeverity,
)

__all__ = [
    # Bill models
    "BillCategory",
    "BillLineItem",
    "BillStatus",
    "ConfirmedBill",
    "DocumentType",
    "EnhancedImage",
    "ExtractedBillData",
    "ImageQuality",
    "ImageUpload",
    "PaymentStatus",
    "QueryResult",
    "StructuredQuery",
    "ValidationIssue",
    "ValidationResult",
    "VendorInfo",
    # Audit models
    "AuditEvent",
    "AuditEventBuilder",
    "AuditEventType",
    "AuditSeverity",
]
