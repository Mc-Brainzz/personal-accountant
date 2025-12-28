"""
Main Orchestrator for Personal Accountant

This module ties together all the components and defines the
end-to-end flows for:
1. Bill Upload (image → enhanced → OCR → validate → confirm → save)
2. Query (question → parse → execute → respond)

DESIGN DECISION: The orchestrator enforces the boundaries:
- No data persists without human confirmation
- No query answers without data lookup
- Every step is audited

This is the "glue" that ensures the system works correctly
even when individual components might behave unexpectedly.
"""

from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

from src.agents import BillUploadAgent, QueryAgent
from src.audit import AuditLogger, create_correlation_id
from src.config import get_settings
from src.models.bill import (
    BillCategory,
    BillStatus,
    ConfirmedBill,
    EnhancedImage,
    ExtractedBillData,
    ImageUpload,
    PaymentStatus,
    QueryResult,
    StructuredQuery,
    ValidationResult,
)
from src.queries import QueryExecutor
from src.services.image import CloudinaryImageService
from src.services.ocr import DocumentTypeRejectedError, MindeeOCRService
from src.services.storage import (
    BillStorageInterface,
    AuditStorageInterface,
    GoogleSheetsBillStorage,
    GoogleSheetsAuditStorage,
    GoogleSheetsClient,
)
from src.validation import BillValidator


class BillUploadFlow:
    """
    Orchestrates the bill upload flow.
    
    Flow:
    1. Upload → Create ImageUpload record
    2. Enhance → Send to Cloudinary, quality check
    3. Extract → Send to Mindee, validate document type
    4. Validate → Two-stage validation
    5. Review → Present to user (PAUSE - require confirmation)
    6. Confirm → User explicitly approves
    7. Save → Persist to storage
    
    Human confirmation (step 6) is MANDATORY.
    The system NEVER auto-saves.
    """
    
    def __init__(
        self,
        image_service: Optional[CloudinaryImageService] = None,
        ocr_service: Optional[MindeeOCRService] = None,
        validator: Optional[BillValidator] = None,
        bill_agent: Optional[BillUploadAgent] = None,
        bill_storage: Optional[BillStorageInterface] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        # Initialize services (lazy loading where possible)
        self._image_service = image_service or CloudinaryImageService()
        self._ocr_service = ocr_service or MindeeOCRService()
        self._bill_storage = bill_storage
        self._validator = validator or BillValidator(bill_storage)
        self._bill_agent = bill_agent or BillUploadAgent()
        self._audit_logger = audit_logger
    
    async def process_image(
        self,
        image_bytes: bytes,
        filename: str,
        file_size: int,
        mime_type: str,
        correlation_id: Optional[UUID] = None,
    ) -> tuple[ImageUpload, EnhancedImage, bool, str]:
        """
        Process an uploaded image (enhance and quality check).
        
        Returns:
            (upload, enhanced, can_proceed, message)
            
        If can_proceed is False, user should retake the photo.
        """
        correlation_id = correlation_id or create_correlation_id()
        
        # Create upload record
        upload = ImageUpload(
            original_filename=filename,
            file_size_bytes=file_size,
            mime_type=mime_type,
        )
        
        # Audit: image uploaded
        if self._audit_logger:
            await self._audit_logger.log_image_uploaded(
                upload_id=upload.upload_id,
                filename=filename,
                file_size=file_size,
                correlation_id=correlation_id,
            )
        
        # Enhance image
        try:
            enhanced = await self._image_service.enhance_image(image_bytes, upload)
        except Exception as e:
            if self._audit_logger:
                await self._audit_logger.log_external_service_error(
                    service="cloudinary",
                    error_message=str(e),
                    correlation_id=correlation_id,
                )
            raise
        
        # Check quality
        can_proceed, message = self._image_service.should_proceed_with_ocr(enhanced)
        
        # Audit
        if self._audit_logger:
            if can_proceed:
                await self._audit_logger.log_image_enhanced(
                    upload_id=upload.upload_id,
                    quality=enhanced.quality_assessment.value,
                    enhancements=enhanced.enhancement_applied,
                    correlation_id=correlation_id,
                )
            else:
                await self._audit_logger.log_image_quality_failed(
                    upload_id=upload.upload_id,
                    quality=enhanced.quality_assessment.value,
                    issues=enhanced.quality_issues,
                    correlation_id=correlation_id,
                )
        
        return upload, enhanced, can_proceed, message
    
    async def extract_bill_data(
        self,
        enhanced_image_url: str,
        upload_id: UUID,
        correlation_id: Optional[UUID] = None,
    ) -> tuple[ExtractedBillData, bool, str]:
        """
        Extract bill data from enhanced image using OCR.
        
        Returns:
            (extracted_data, can_proceed, message)
            
        If can_proceed is False, document was rejected or extraction failed.
        """
        correlation_id = correlation_id or create_correlation_id()
        
        try:
            extracted = await self._ocr_service.extract_bill_data(
                image_url=enhanced_image_url,
                upload_id=upload_id,
            )
        except DocumentTypeRejectedError as e:
            # Document is not a bill/invoice
            if self._audit_logger:
                await self._audit_logger.log_document_rejected(
                    extraction_id=uuid4(),
                    detected_type=e.detected_type,
                    correlation_id=correlation_id,
                )
            return None, False, str(e)
        except Exception as e:
            if self._audit_logger:
                await self._audit_logger.log_external_service_error(
                    service="mindee",
                    error_message=str(e),
                    correlation_id=correlation_id,
                )
            raise
        
        # Check extraction quality
        can_proceed, message = self._ocr_service.should_proceed_with_extraction(extracted)
        
        # Audit
        if self._audit_logger:
            await self._audit_logger.log_ocr_completed(
                extraction_id=extracted.extraction_id,
                document_type=extracted.document_type.value,
                confidence=extracted.confidence_score,
                correlation_id=correlation_id,
            )
        
        return extracted, can_proceed, message
    
    async def validate_extraction(
        self,
        extracted: ExtractedBillData,
        correlation_id: Optional[UUID] = None,
    ) -> tuple[ValidationResult, str]:
        """
        Validate extracted bill data.
        
        Returns:
            (validation_result, user_message)
        """
        correlation_id = correlation_id or create_correlation_id()
        
        result = await self._validator.validate(extracted)
        message = self._validator.get_user_friendly_summary(result)
        
        # Audit validation failures
        if self._audit_logger and not result.is_valid:
            issues = [
                {"field": i.field, "type": i.issue_type, "message": i.message}
                for i in result.issues
            ]
            stage = "schema" if not result.schema_valid else "semantic"
            await self._audit_logger.log_validation_failed(
                extraction_id=extracted.extraction_id,
                stage=stage,
                issues=issues,
                correlation_id=correlation_id,
            )
        
        return result, message
    
    async def suggest_category(
        self,
        extracted: ExtractedBillData,
    ) -> tuple[BillCategory, float, str]:
        """
        Get AI-suggested category for the bill.
        
        Returns:
            (category, confidence, reasoning)
        """
        suggestion = await self._bill_agent.suggest_category(extracted)
        return suggestion.category, suggestion.confidence, suggestion.reasoning
    
    async def confirm_and_save(
        self,
        extracted: ExtractedBillData,
        category: BillCategory,
        vendor_name: str,
        total_amount: Decimal,
        bill_date: datetime,
        due_date: Optional[datetime] = None,
        notes: Optional[str] = None,
        original_image_url: Optional[str] = None,
        enhanced_image_url: Optional[str] = None,
        correlation_id: Optional[UUID] = None,
    ) -> ConfirmedBill:
        """
        Confirm and save the bill.
        
        CRITICAL: This is called ONLY after explicit user confirmation.
        
        Args:
            All required bill fields (user may have edited)
            
        Returns:
            The saved ConfirmedBill
        """
        correlation_id = correlation_id or create_correlation_id()
        
        # Create confirmed bill
        bill = ConfirmedBill(
            extraction_id=extracted.extraction_id,
            vendor_name=vendor_name,
            category=category,
            total_amount=total_amount,
            bill_date=bill_date,
            vendor_info=extracted.vendor,
            bill_number=extracted.bill_number,
            due_date=due_date,
            billing_period_start=extracted.billing_period_start,
            billing_period_end=extracted.billing_period_end,
            subtotal=extracted.subtotal,
            tax_amount=extracted.tax_amount,
            line_items=extracted.line_items,
            status=BillStatus.CONFIRMED,
            payment_status=PaymentStatus.UNPAID,
            notes=notes,
            original_image_url=original_image_url,
            enhanced_image_url=enhanced_image_url,
        )
        
        # Audit: user confirmed
        if self._audit_logger:
            await self._audit_logger.log_user_confirmed(
                bill_id=bill.id,
                extraction_id=extracted.extraction_id,
                correlation_id=correlation_id,
            )
        
        # Save to storage
        if self._bill_storage:
            await self._bill_storage.save_bill(bill)
            
            # Audit: bill saved
            if self._audit_logger:
                await self._audit_logger.log_bill_saved(
                    bill_id=bill.id,
                    vendor=vendor_name,
                    amount=str(total_amount),
                    correlation_id=correlation_id,
                )
        
        return bill
    
    async def reject_extraction(
        self,
        extracted: ExtractedBillData,
        reason: Optional[str] = None,
        correlation_id: Optional[UUID] = None,
    ) -> None:
        """
        Record that user rejected the extraction.
        
        This is called when user chooses not to save after reviewing.
        """
        correlation_id = correlation_id or create_correlation_id()
        
        if self._audit_logger:
            await self._audit_logger.log_user_rejected(
                extraction_id=extracted.extraction_id,
                reason=reason,
                correlation_id=correlation_id,
            )


class QueryFlow:
    """
    Orchestrates the RAG query flow.
    
    CRITICAL BOUNDARIES:
    1. User question → LLM parses intent
    2. Intent → StructuredQuery (deterministic)
    3. Query → Execute on storage (deterministic)
    4. Results → LLM generates response
    
    The LLM is NEVER allowed to answer directly.
    It can only work with actual data from storage.
    """
    
    def __init__(
        self,
        query_agent: Optional[QueryAgent] = None,
        bill_storage: Optional[BillStorageInterface] = None,
        audit_logger: Optional[AuditLogger] = None,
    ):
        self._query_agent = query_agent or QueryAgent()
        self._bill_storage = bill_storage
        self._query_executor = QueryExecutor(bill_storage) if bill_storage else None
        self._audit_logger = audit_logger
    
    async def answer_question(
        self,
        question: str,
        correlation_id: Optional[UUID] = None,
    ) -> tuple[str, QueryResult, StructuredQuery]:
        """
        Answer a user's question using RAG.
        
        FLOW:
        1. Parse question → Intent
        2. Intent → StructuredQuery
        3. Execute query → Results
        4. Results → Natural language response
        
        The LLM NEVER sees the storage directly.
        It only sees query results.
        
        Returns:
            (answer, query_result, structured_query)
        """
        correlation_id = correlation_id or create_correlation_id()
        
        # Check if storage is configured
        if not self._query_executor:
            return (
                "I can't answer questions yet because the data storage isn't configured. "
                "Please set up Google Sheets first.",
                QueryResult(
                    query_id=uuid4(),
                    success=False,
                    error_message="Storage not configured",
                    data_found=False,
                    result_count=0,
                    query_description="No storage",
                ),
                None,
            )
        
        # Step 1: Parse question to intent
        intent = await self._query_agent.parse_question(question)
        
        # Step 2: Convert intent to structured query
        query = self._query_agent.intent_to_query(intent, question)
        
        # Step 3: Execute query
        result = await self._query_executor.execute(query)
        
        # Audit
        if self._audit_logger:
            await self._audit_logger.log_query_executed(
                query_id=query.query_id,
                query_type=query.query_type,
                result_count=result.result_count,
                correlation_id=correlation_id,
            )
        
        # Step 4: Generate response from results
        response = await self._query_agent.generate_response(query, result)
        
        return response.response, result, query


def create_app_components(
    use_storage: bool = True,
) -> tuple[BillUploadFlow, QueryFlow, Optional[GoogleSheetsClient]]:
    """
    Factory function to create all application components.
    
    Args:
        use_storage: Whether to initialize Google Sheets storage.
                    Set to False for testing without storage.
                    
    Returns:
        (bill_upload_flow, query_flow, sheets_client)
    """
    sheets_client = None
    bill_storage = None
    audit_storage = None
    audit_logger = None
    
    if use_storage:
        try:
            sheets_client = GoogleSheetsClient()
            bill_storage = GoogleSheetsBillStorage(sheets_client)
            audit_storage = GoogleSheetsAuditStorage(sheets_client)
            audit_logger = AuditLogger(audit_storage)
        except Exception as e:
            # Storage not configured - continue without it
            print(f"Warning: Storage not configured: {e}")
            sheets_client = None
            bill_storage = None
            audit_storage = None
            audit_logger = AuditLogger()  # Local-only logging
    else:
        audit_logger = AuditLogger()  # Local-only logging
    
    # Create flows
    bill_upload_flow = BillUploadFlow(
        bill_storage=bill_storage,
        audit_logger=audit_logger,
    )
    
    query_flow = QueryFlow(
        bill_storage=bill_storage,
        audit_logger=audit_logger,
    )
    
    return bill_upload_flow, query_flow, sheets_client
