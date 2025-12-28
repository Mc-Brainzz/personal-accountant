"""
Core Data Models for Personal Accountant

These models define the strict schemas for all data flowing through the system.
They are designed to:
1. Enforce type safety at runtime
2. Provide clear validation error messages
3. Be serializable for storage and logging
4. Support the audit trail

DESIGN DECISION: We use Pydantic v2 with strict mode where appropriate.
This catches type coercion issues early rather than silently converting values.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Optional
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


# =============================================================================
# ENUMS - Finite set of valid values
# =============================================================================

class BillCategory(str, Enum):
    """
    Supported bill categories.
    
    DESIGN DECISION: Using explicit categories rather than free text ensures
    consistent categorization and enables reliable querying.
    """
    ELECTRICITY = "electricity"
    WATER = "water"
    GAS = "gas"
    INTERNET = "internet"
    MOBILE = "mobile"
    GROCERIES = "groceries"
    MEDICAL = "medical"
    INSURANCE = "insurance"
    RENT = "rent"
    MAINTENANCE = "maintenance"
    FUEL = "fuel"
    OTHER = "other"


class BillStatus(str, Enum):
    """
    Bill processing status.
    
    CRITICAL: Bills can only be CONFIRMED by explicit user action.
    The system NEVER auto-confirms.
    """
    PENDING_REVIEW = "pending_review"  # OCR complete, awaiting user review
    CONFIRMED = "confirmed"             # User has explicitly confirmed
    REJECTED = "rejected"               # User rejected the extraction
    CORRECTION_NEEDED = "correction_needed"  # User wants to edit


class PaymentStatus(str, Enum):
    """Payment status for a bill."""
    UNPAID = "unpaid"
    PAID = "paid"
    PARTIAL = "partial"
    OVERDUE = "overdue"


class DocumentType(str, Enum):
    """
    Document types we accept.
    
    DESIGN DECISION: We ONLY accept financial documents.
    Anything else is loudly rejected.
    """
    INVOICE = "invoice"
    UTILITY_BILL = "utility_bill"
    RECEIPT = "receipt"
    UNKNOWN = "unknown"  # Will be rejected


class ImageQuality(str, Enum):
    """Image quality assessment result."""
    GOOD = "good"
    ACCEPTABLE = "acceptable"
    POOR = "poor"  # Requires user to retake
    UNUSABLE = "unusable"  # Hard reject


# =============================================================================
# CORE BILL MODEL
# =============================================================================

class BillLineItem(BaseModel):
    """
    Individual line item on a bill.
    
    For utility bills, this might be:
    - Units consumed
    - Fixed charges
    - Taxes
    """
    model_config = ConfigDict(str_strip_whitespace=True)
    
    description: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Description of the line item"
    )
    amount: Decimal = Field(
        ...,
        ge=0,
        decimal_places=2,
        description="Amount in INR"
    )
    quantity: Optional[Decimal] = Field(
        default=None,
        ge=0,
        description="Quantity (e.g., units of electricity)"
    )
    unit: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Unit of measurement (e.g., kWh, liters)"
    )


class VendorInfo(BaseModel):
    """
    Vendor/Company information extracted from bill.
    
    DESIGN DECISION: Vendor name is required for all bills.
    This enables grouping and querying by vendor.
    """
    model_config = ConfigDict(str_strip_whitespace=True)
    
    name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Vendor/Company name"
    )
    address: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Vendor address if available"
    )
    contact: Optional[str] = Field(
        default=None,
        max_length=100,
        description="Contact number or email"
    )
    account_number: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Customer account/consumer number"
    )


class ExtractedBillData(BaseModel):
    """
    Data extracted from OCR.
    
    CRITICAL: This is PROPOSED data, NOT verified.
    It MUST go through human confirmation before being trusted.
    
    This model represents what the OCR thinks it saw.
    All fields are optional because OCR might fail to extract some.
    """
    model_config = ConfigDict(str_strip_whitespace=True)
    
    # Extraction metadata
    extraction_id: UUID = Field(
        default_factory=uuid4,
        description="Unique ID for this extraction attempt"
    )
    extracted_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When extraction was performed"
    )
    confidence_score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Overall confidence in extraction (0-1)"
    )
    document_type: DocumentType = Field(
        ...,
        description="Detected document type"
    )
    
    # Core bill data (all optional - OCR might miss these)
    vendor: Optional[VendorInfo] = None
    bill_number: Optional[str] = Field(
        default=None,
        max_length=50,
        description="Bill/Invoice number"
    )
    bill_date: Optional[date] = Field(
        default=None,
        description="Date on the bill"
    )
    due_date: Optional[date] = Field(
        default=None,
        description="Payment due date"
    )
    billing_period_start: Optional[date] = None
    billing_period_end: Optional[date] = None
    
    # Amounts
    subtotal: Optional[Decimal] = Field(
        default=None,
        ge=0,
        decimal_places=2
    )
    tax_amount: Optional[Decimal] = Field(
        default=None,
        ge=0,
        decimal_places=2
    )
    total_amount: Optional[Decimal] = Field(
        default=None,
        ge=0,
        decimal_places=2,
        description="Total amount to pay"
    )
    
    # Line items (if extractable)
    line_items: list[BillLineItem] = Field(default_factory=list)
    
    # Category suggestion (LLM assisted)
    suggested_category: Optional[BillCategory] = None
    
    # Raw OCR text for debugging
    raw_ocr_text: Optional[str] = Field(
        default=None,
        description="Raw text from OCR for debugging"
    )
    
    @field_validator('confidence_score')
    @classmethod
    def warn_low_confidence(cls, v: float) -> float:
        """Flag low confidence extractions."""
        if v < 0.7:
            # This will be handled by the UI to show a warning
            pass
        return v


class ConfirmedBill(BaseModel):
    """
    A bill that has been CONFIRMED by the user.
    
    CRITICAL: Only ConfirmedBill objects are persisted to storage.
    The user MUST explicitly confirm before we create this.
    
    This model has stricter requirements than ExtractedBillData
    because required fields must be present.
    """
    model_config = ConfigDict(str_strip_whitespace=True)
    
    # Identity
    id: UUID = Field(
        default_factory=uuid4,
        description="Unique bill ID"
    )
    
    # Traceability back to extraction
    extraction_id: UUID = Field(
        ...,
        description="ID of the extraction this was confirmed from"
    )
    
    # Timestamps
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the bill was confirmed and saved"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="Last update timestamp"
    )
    
    # REQUIRED fields for a confirmed bill
    vendor_name: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description="Vendor name (required)"
    )
    category: BillCategory = Field(
        ...,
        description="Bill category (required)"
    )
    total_amount: Annotated[
        Decimal,
        Field(ge=0, decimal_places=2, description="Total amount in INR (required)")
    ]
    bill_date: date = Field(
        ...,
        description="Date on the bill (required)"
    )
    
    # Optional but valuable fields
    vendor_info: Optional[VendorInfo] = None
    bill_number: Optional[str] = None
    due_date: Optional[date] = None
    billing_period_start: Optional[date] = None
    billing_period_end: Optional[date] = None
    subtotal: Optional[Decimal] = None
    tax_amount: Optional[Decimal] = None
    line_items: list[BillLineItem] = Field(default_factory=list)
    
    # Status tracking
    status: BillStatus = Field(
        default=BillStatus.CONFIRMED,
        description="Bill status"
    )
    payment_status: PaymentStatus = Field(
        default=PaymentStatus.UNPAID,
        description="Payment status"
    )
    paid_date: Optional[date] = None
    
    # User notes
    notes: Optional[str] = Field(
        default=None,
        max_length=1000,
        description="User notes about this bill"
    )
    
    # Original image reference
    original_image_url: Optional[str] = Field(
        default=None,
        description="URL to original uploaded image"
    )
    enhanced_image_url: Optional[str] = Field(
        default=None,
        description="URL to enhanced image"
    )
    
    @model_validator(mode='after')
    def validate_dates(self) -> 'ConfirmedBill':
        """Validate date relationships."""
        if self.due_date and self.bill_date:
            if self.due_date < self.bill_date:
                raise ValueError("Due date cannot be before bill date")
        
        if self.billing_period_start and self.billing_period_end:
            if self.billing_period_end < self.billing_period_start:
                raise ValueError("Billing period end cannot be before start")
        
        if self.paid_date and self.bill_date:
            if self.paid_date < self.bill_date:
                raise ValueError("Paid date cannot be before bill date")
        
        return self


# =============================================================================
# IMAGE PROCESSING MODELS
# =============================================================================

class ImageUpload(BaseModel):
    """Represents an uploaded image before processing."""
    
    upload_id: UUID = Field(
        default_factory=uuid4,
        description="Unique upload identifier"
    )
    uploaded_at: datetime = Field(
        default_factory=datetime.utcnow
    )
    original_filename: str
    file_size_bytes: int = Field(ge=0)
    mime_type: str
    
    @field_validator('mime_type')
    @classmethod
    def validate_mime_type(cls, v: str) -> str:
        """Only allow image types."""
        allowed = {'image/jpeg', 'image/png', 'image/webp'}
        if v.lower() not in allowed:
            raise ValueError(f"Unsupported image type: {v}. Allowed: {allowed}")
        return v.lower()


class EnhancedImage(BaseModel):
    """Result of image enhancement."""
    
    upload_id: UUID = Field(
        ...,
        description="Original upload this enhancement is for"
    )
    enhanced_at: datetime = Field(
        default_factory=datetime.utcnow
    )
    cloudinary_url: str = Field(
        ...,
        description="URL to enhanced image on Cloudinary"
    )
    quality_assessment: ImageQuality
    quality_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Quality score (0-1)"
    )
    enhancement_applied: list[str] = Field(
        default_factory=list,
        description="List of enhancements applied"
    )
    
    # If quality is poor/unusable, explain why
    quality_issues: list[str] = Field(
        default_factory=list,
        description="List of detected quality issues"
    )


# =============================================================================
# VALIDATION MODELS
# =============================================================================

class ValidationIssue(BaseModel):
    """A single validation issue found."""
    
    field: str = Field(
        ...,
        description="Field with the issue"
    )
    issue_type: str = Field(
        ...,
        description="Type of issue (e.g., 'missing', 'invalid_format', 'suspicious_value')"
    )
    message: str = Field(
        ...,
        description="Human-readable description of the issue"
    )
    severity: str = Field(
        ...,
        pattern="^(error|warning|info)$",
        description="Issue severity"
    )
    suggested_fix: Optional[str] = Field(
        default=None,
        description="Suggested fix if available"
    )


class ValidationResult(BaseModel):
    """
    Result of the two-stage validation.
    
    Stage 1: Schema validation (types, required fields)
    Stage 2: Semantic validation (logic checks)
    """
    
    extraction_id: UUID = Field(
        ...,
        description="ID of the extraction being validated"
    )
    validated_at: datetime = Field(
        default_factory=datetime.utcnow
    )
    
    # Stage results
    schema_valid: bool = Field(
        ...,
        description="Did schema validation pass?"
    )
    semantic_valid: bool = Field(
        ...,
        description="Did semantic validation pass?"
    )
    
    # Overall result
    is_valid: bool = Field(
        ...,
        description="Overall validation result"
    )
    can_proceed_with_review: bool = Field(
        ...,
        description="Can we show this to user for review?"
    )
    
    # Issues found
    issues: list[ValidationIssue] = Field(
        default_factory=list,
        description="All validation issues found"
    )
    
    # Warnings don't block but should be shown
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-blocking warnings"
    )
    
    @property
    def has_errors(self) -> bool:
        """Check if there are any error-level issues."""
        return any(issue.severity == "error" for issue in self.issues)
    
    @property
    def error_count(self) -> int:
        """Count error-level issues."""
        return sum(1 for issue in self.issues if issue.severity == "error")


# =============================================================================
# QUERY MODELS (for RAG system)
# =============================================================================

class StructuredQuery(BaseModel):
    """
    A structured query converted from natural language.
    
    CRITICAL: The LLM converts user questions to this format.
    The query is then executed DETERMINISTICALLY on stored data.
    The LLM is FORBIDDEN from answering directly.
    """
    
    query_id: UUID = Field(
        default_factory=uuid4
    )
    original_question: str = Field(
        ...,
        description="Original natural language question"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow
    )
    
    # Query parameters
    query_type: str = Field(
        ...,
        pattern="^(lookup|aggregate|compare|list|exists)$",
        description="Type of query to execute"
    )
    
    # Filters
    category_filter: Optional[BillCategory] = None
    vendor_filter: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    payment_status_filter: Optional[PaymentStatus] = None
    
    # For aggregations
    aggregation_type: Optional[str] = Field(
        default=None,
        pattern="^(sum|count|average|min|max)$"
    )
    group_by: Optional[str] = Field(
        default=None,
        pattern="^(category|vendor|month|year)$"
    )
    
    # Limit results
    limit: int = Field(
        default=10,
        ge=1,
        le=100
    )


class QueryResult(BaseModel):
    """
    Result of executing a structured query.
    
    This is what the LLM uses to generate a natural language response.
    """
    
    query_id: UUID
    executed_at: datetime = Field(
        default_factory=datetime.utcnow
    )
    
    # Success/failure
    success: bool
    error_message: Optional[str] = None
    
    # Results
    data_found: bool = Field(
        ...,
        description="Was any data found?"
    )
    result_count: int = Field(
        ge=0,
        description="Number of results"
    )
    
    # The actual data (for LLM to use)
    results: list[dict] = Field(
        default_factory=list,
        description="Query results as list of dicts"
    )
    
    # Aggregation result if applicable
    aggregation_result: Optional[dict] = None
    
    # For the LLM to understand context
    query_description: str = Field(
        ...,
        description="Human-readable description of what was queried"
    )
