"""
Tests for Personal Accountant

Test strategy:
1. Unit tests for individual components (models, validators)
2. Integration tests for flows (with mocked external services)
3. No real API calls in tests (use mocks)
"""

import pytest
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from src.models.bill import (
    BillCategory,
    BillLineItem,
    BillStatus,
    ConfirmedBill,
    DocumentType,
    ExtractedBillData,
    ImageQuality,
    PaymentStatus,
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


class TestBillModels:
    """Tests for bill-related Pydantic models."""
    
    def test_vendor_info_creation(self):
        """Test VendorInfo model creation."""
        vendor = VendorInfo(
            name="BESCOM",
            address="Bangalore",
            contact="1912",
            account_number="ABC123",
        )
        assert vendor.name == "BESCOM"
        assert vendor.address == "Bangalore"
    
    def test_vendor_info_strips_whitespace(self):
        """Test that whitespace is stripped from vendor name."""
        vendor = VendorInfo(name="  BESCOM  ")
        assert vendor.name == "BESCOM"
    
    def test_bill_line_item_creation(self):
        """Test BillLineItem model creation."""
        item = BillLineItem(
            description="Energy Charge",
            amount=Decimal("500.00"),
            quantity=Decimal("150"),
            unit="kWh",
        )
        assert item.description == "Energy Charge"
        assert item.amount == Decimal("500.00")
    
    def test_bill_line_item_rejects_negative_amount(self):
        """Test that negative amounts are rejected."""
        with pytest.raises(ValueError):
            BillLineItem(
                description="Test",
                amount=Decimal("-100"),
            )
    
    def test_extracted_bill_data_creation(self):
        """Test ExtractedBillData model creation."""
        extracted = ExtractedBillData(
            confidence_score=0.85,
            document_type=DocumentType.INVOICE,
            vendor=VendorInfo(name="Test Vendor"),
            total_amount=Decimal("1000.00"),
            bill_date=date(2024, 12, 15),
        )
        assert extracted.confidence_score == 0.85
        assert extracted.total_amount == Decimal("1000.00")
    
    def test_extracted_bill_data_confidence_bounds(self):
        """Test confidence score must be between 0 and 1."""
        with pytest.raises(ValueError):
            ExtractedBillData(
                confidence_score=1.5,  # Invalid
                document_type=DocumentType.INVOICE,
            )
    
    def test_confirmed_bill_creation(self):
        """Test ConfirmedBill model creation."""
        bill = ConfirmedBill(
            extraction_id=uuid4(),
            vendor_name="BESCOM",
            category=BillCategory.ELECTRICITY,
            total_amount=Decimal("1500.00"),
            bill_date=date(2024, 12, 1),
        )
        assert bill.vendor_name == "BESCOM"
        assert bill.status == BillStatus.CONFIRMED
        assert bill.payment_status == PaymentStatus.UNPAID
    
    def test_confirmed_bill_date_validation(self):
        """Test that due_date cannot be before bill_date."""
        with pytest.raises(ValueError, match="Due date cannot be before bill date"):
            ConfirmedBill(
                extraction_id=uuid4(),
                vendor_name="Test",
                category=BillCategory.OTHER,
                total_amount=Decimal("100"),
                bill_date=date(2024, 12, 15),
                due_date=date(2024, 12, 1),  # Before bill_date
            )
    
    def test_confirmed_bill_billing_period_validation(self):
        """Test billing period end cannot be before start."""
        with pytest.raises(ValueError, match="Billing period end cannot be before start"):
            ConfirmedBill(
                extraction_id=uuid4(),
                vendor_name="Test",
                category=BillCategory.OTHER,
                total_amount=Decimal("100"),
                bill_date=date(2024, 12, 15),
                billing_period_start=date(2024, 12, 1),
                billing_period_end=date(2024, 11, 1),  # Before start
            )


class TestAuditModels:
    """Tests for audit-related models."""
    
    def test_audit_event_creation(self):
        """Test AuditEvent model creation."""
        event = AuditEvent(
            event_type=AuditEventType.IMAGE_UPLOADED,
            description="Test image uploaded",
        )
        assert event.event_type == AuditEventType.IMAGE_UPLOADED
        assert event.severity == AuditSeverity.INFO
    
    def test_audit_event_to_log_dict(self):
        """Test conversion to log dictionary."""
        event = AuditEvent(
            event_type=AuditEventType.BILL_SAVED,
            description="Bill saved successfully",
            details={"vendor": "BESCOM", "amount": "1000"},
        )
        log_dict = event.to_log_dict()
        assert "event_id" in log_dict
        assert log_dict["event_type"] == "bill_saved"
        assert log_dict["details"]["vendor"] == "BESCOM"
    
    def test_audit_event_to_sheets_row(self):
        """Test conversion to sheets row."""
        event = AuditEvent(
            event_type=AuditEventType.USER_CONFIRMED,
            description="User confirmed bill",
            is_user_action=True,
        )
        row = event.to_sheets_row()
        assert len(row) == 11  # Expected number of columns
        assert row[2] == "user_confirmed"  # event_type
        assert row[10] == "True"  # is_user_action
    
    def test_audit_event_builder_image_uploaded(self):
        """Test AuditEventBuilder.image_uploaded."""
        correlation_id = uuid4()
        upload_id = uuid4()
        
        event = AuditEventBuilder.image_uploaded(
            upload_id=upload_id,
            filename="test.jpg",
            file_size=1024,
            correlation_id=correlation_id,
        )
        
        assert event.event_type == AuditEventType.IMAGE_UPLOADED
        assert event.entity_id == upload_id
        assert event.correlation_id == correlation_id
        assert event.is_user_action is True
    
    def test_audit_event_builder_user_confirmed(self):
        """Test AuditEventBuilder.user_confirmed."""
        bill_id = uuid4()
        extraction_id = uuid4()
        correlation_id = uuid4()
        
        event = AuditEventBuilder.user_confirmed(
            bill_id=bill_id,
            extraction_id=extraction_id,
            correlation_id=correlation_id,
        )
        
        assert event.event_type == AuditEventType.USER_CONFIRMED
        assert event.entity_id == bill_id
        assert event.is_user_action is True


class TestValidationResult:
    """Tests for ValidationResult model."""
    
    def test_validation_result_has_errors(self):
        """Test has_errors property."""
        result = ValidationResult(
            extraction_id=uuid4(),
            schema_valid=False,
            semantic_valid=False,
            is_valid=False,
            can_proceed_with_review=False,
            issues=[
                ValidationIssue(
                    field="total_amount",
                    issue_type="missing",
                    message="Total amount required",
                    severity="error",
                ),
            ],
        )
        assert result.has_errors is True
        assert result.error_count == 1
    
    def test_validation_result_warnings_only(self):
        """Test that warnings don't count as errors."""
        result = ValidationResult(
            extraction_id=uuid4(),
            schema_valid=True,
            semantic_valid=True,
            is_valid=True,
            can_proceed_with_review=True,
            issues=[
                ValidationIssue(
                    field="bill_date",
                    issue_type="future_date",
                    message="Date in future",
                    severity="warning",
                ),
            ],
        )
        assert result.has_errors is False
        assert result.error_count == 0


class TestBillCategories:
    """Tests for bill category enum."""
    
    def test_all_categories_exist(self):
        """Test that expected categories exist."""
        expected = [
            "electricity", "water", "gas", "internet", "mobile",
            "groceries", "medical", "insurance", "rent", "maintenance",
            "fuel", "other",
        ]
        for cat in expected:
            assert BillCategory(cat) is not None
    
    def test_category_values(self):
        """Test category string values."""
        assert BillCategory.ELECTRICITY.value == "electricity"
        assert BillCategory.GROCERIES.value == "groceries"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
