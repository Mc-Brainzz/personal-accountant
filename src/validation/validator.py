"""
Two-Stage Validation Pipeline

DESIGN DECISION: Validation happens in two distinct stages:

STAGE 1 - SCHEMA VALIDATION:
- Type checking
- Required field presence
- Format validation
- This catches OCR errors and malformed data

STAGE 2 - SEMANTIC VALIDATION:
- Business logic checks
- Future date detection
- Absurd amount detection
- Vendor sanity checks
- Duplicate detection
- This catches logically impossible or suspicious data

WHY TWO STAGES:
1. Separation of concerns (structural vs logical)
2. Better error messages (know exactly what kind of issue)
3. Can skip stage 2 if stage 1 fails
4. Stage 2 needs access to storage for duplicate checks

IMPORTANT: Validation NEVER silently fixes issues.
It reports them for human review.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID

from src.config import get_settings
from src.models.bill import (
    BillCategory,
    ExtractedBillData,
    ValidationIssue,
    ValidationResult,
)
from src.services.storage import BillStorageInterface


class BillValidator:
    """
    Validates extracted bill data through a two-stage pipeline.
    
    Stage 1: Schema validation (can run without storage)
    Stage 2: Semantic validation (may need storage for duplicate checks)
    """
    
    def __init__(
        self,
        bill_storage: Optional[BillStorageInterface] = None,
    ):
        """
        Initialize validator.
        
        Args:
            bill_storage: Storage interface for duplicate checking.
                         If None, duplicate checking is skipped.
        """
        self._storage = bill_storage
        self._settings = get_settings().app
    
    def _validate_schema(
        self,
        extracted: ExtractedBillData,
    ) -> tuple[bool, list[ValidationIssue]]:
        """
        Stage 1: Schema validation.
        
        Checks:
        - Required fields presence
        - Type correctness
        - Format validation
        
        Returns: (is_valid, list_of_issues)
        """
        issues = []
        
        # Check required fields
        if extracted.total_amount is None:
            issues.append(ValidationIssue(
                field="total_amount",
                issue_type="missing",
                message="Total amount is required but was not extracted",
                severity="error",
                suggested_fix="Ensure the total amount is clearly visible in the photo",
            ))
        elif extracted.total_amount <= 0:
            issues.append(ValidationIssue(
                field="total_amount",
                issue_type="invalid_value",
                message="Total amount must be greater than zero",
                severity="error",
                suggested_fix="Check if the amount was read correctly",
            ))
        
        if extracted.vendor is None or not extracted.vendor.name:
            issues.append(ValidationIssue(
                field="vendor",
                issue_type="missing",
                message="Vendor/company name is required but was not extracted",
                severity="warning",  # Warning because user can manually enter
                suggested_fix="You'll need to enter the vendor name manually",
            ))
        
        # Bill date validation
        if extracted.bill_date is None:
            issues.append(ValidationIssue(
                field="bill_date",
                issue_type="missing",
                message="Bill date is required but was not extracted",
                severity="warning",
                suggested_fix="You'll need to enter the bill date manually",
            ))
        
        # Check confidence score
        if extracted.confidence_score < 0.5:
            issues.append(ValidationIssue(
                field="confidence_score",
                issue_type="low_confidence",
                message=f"Extraction confidence is low ({extracted.confidence_score:.0%})",
                severity="warning",
                suggested_fix="Please review all fields carefully",
            ))
        
        # Check for empty extraction
        if (
            extracted.total_amount is None
            and extracted.vendor is None
            and extracted.bill_date is None
        ):
            issues.append(ValidationIssue(
                field="extraction",
                issue_type="empty",
                message="No meaningful data could be extracted from this image",
                severity="error",
                suggested_fix="Please try with a clearer photo",
            ))
        
        # Schema is valid if no errors (warnings are okay)
        is_valid = not any(issue.severity == "error" for issue in issues)
        
        return is_valid, issues
    
    def _validate_semantic(
        self,
        extracted: ExtractedBillData,
    ) -> tuple[bool, list[ValidationIssue]]:
        """
        Stage 2: Semantic validation.
        
        Checks:
        - Future dates
        - Absurd amounts
        - Date consistency
        - Vendor name sanity
        
        Returns: (is_valid, list_of_issues)
        """
        issues = []
        today = date.today()
        
        # Future date check (with tolerance)
        max_future_days = self._settings.future_date_tolerance_days
        max_future_date = today + timedelta(days=max_future_days)
        
        if extracted.bill_date and extracted.bill_date > max_future_date:
            issues.append(ValidationIssue(
                field="bill_date",
                issue_type="future_date",
                message=f"Bill date ({extracted.bill_date}) is in the future",
                severity="warning",
                suggested_fix="Please verify the date is correct",
            ))
        
        # Very old date check (might be OCR error)
        min_reasonable_date = today - timedelta(days=365 * 2)  # 2 years ago
        if extracted.bill_date and extracted.bill_date < min_reasonable_date:
            issues.append(ValidationIssue(
                field="bill_date",
                issue_type="suspicious_date",
                message=f"Bill date ({extracted.bill_date}) seems unusually old",
                severity="warning",
                suggested_fix="Please verify the date was read correctly",
            ))
        
        # Due date after bill date
        if (
            extracted.due_date
            and extracted.bill_date
            and extracted.due_date < extracted.bill_date
        ):
            issues.append(ValidationIssue(
                field="due_date",
                issue_type="inconsistent",
                message="Due date is before bill date",
                severity="warning",
                suggested_fix="Please verify both dates",
            ))
        
        # Absurd amount check
        max_amount = Decimal(str(self._settings.max_bill_amount_inr))
        if extracted.total_amount and extracted.total_amount > max_amount:
            issues.append(ValidationIssue(
                field="total_amount",
                issue_type="suspicious_value",
                message=f"Amount (₹{extracted.total_amount:,.2f}) seems unusually high",
                severity="warning",
                suggested_fix="Please verify this amount is correct",
            ))
        
        # Very small amount check (might be OCR error)
        if extracted.total_amount and extracted.total_amount < Decimal("1"):
            issues.append(ValidationIssue(
                field="total_amount",
                issue_type="suspicious_value",
                message=f"Amount (₹{extracted.total_amount}) seems unusually low",
                severity="warning",
                suggested_fix="Please verify this amount is correct",
            ))
        
        # Subtotal + tax consistency
        if (
            extracted.subtotal
            and extracted.tax_amount
            and extracted.total_amount
        ):
            expected_total = extracted.subtotal + extracted.tax_amount
            # Allow 5% tolerance for rounding
            tolerance = expected_total * Decimal("0.05")
            diff = abs(extracted.total_amount - expected_total)
            
            if diff > tolerance and diff > Decimal("10"):  # And more than ₹10
                issues.append(ValidationIssue(
                    field="total_amount",
                    issue_type="inconsistent",
                    message=(
                        f"Total (₹{extracted.total_amount}) doesn't match "
                        f"subtotal + tax (₹{expected_total})"
                    ),
                    severity="warning",
                    suggested_fix="Please verify the amounts",
                ))
        
        # Billing period consistency
        if (
            extracted.billing_period_start
            and extracted.billing_period_end
            and extracted.billing_period_end < extracted.billing_period_start
        ):
            issues.append(ValidationIssue(
                field="billing_period",
                issue_type="inconsistent",
                message="Billing period end is before start",
                severity="warning",
                suggested_fix="Please verify the billing period",
            ))
        
        # Vendor name sanity (not just numbers/symbols)
        if extracted.vendor and extracted.vendor.name:
            name = extracted.vendor.name
            # Check if name is mostly non-alphanumeric
            alpha_count = sum(1 for c in name if c.isalpha())
            if len(name) > 0 and alpha_count / len(name) < 0.3:
                issues.append(ValidationIssue(
                    field="vendor",
                    issue_type="suspicious_value",
                    message="Vendor name looks unusual (too many numbers/symbols)",
                    severity="warning",
                    suggested_fix="Please verify the vendor name",
                ))
        
        # Semantic validation passes if no errors
        is_valid = not any(issue.severity == "error" for issue in issues)
        
        return is_valid, issues
    
    async def _check_duplicates(
        self,
        extracted: ExtractedBillData,
    ) -> list[ValidationIssue]:
        """
        Check for potential duplicate bills.
        
        This requires storage access.
        """
        issues = []
        
        if self._storage is None:
            return issues
        
        if not extracted.vendor or not extracted.bill_date:
            return issues
        
        try:
            is_duplicate = await self._storage.bill_exists(
                vendor=extracted.vendor.name,
                bill_number=extracted.bill_number,
                bill_date=extracted.bill_date,
            )
            
            if is_duplicate:
                issues.append(ValidationIssue(
                    field="duplicate",
                    issue_type="potential_duplicate",
                    message=(
                        f"A bill from {extracted.vendor.name} dated "
                        f"{extracted.bill_date} may already exist"
                    ),
                    severity="warning",
                    suggested_fix="Please verify this isn't a duplicate entry",
                ))
        except Exception:
            # Don't fail validation due to storage errors
            pass
        
        return issues
    
    async def validate(
        self,
        extracted: ExtractedBillData,
        check_duplicates: bool = True,
    ) -> ValidationResult:
        """
        Run full two-stage validation pipeline.
        
        Args:
            extracted: The extracted bill data to validate
            check_duplicates: Whether to check for duplicates (requires storage)
            
        Returns:
            ValidationResult with all issues found
        """
        all_issues = []
        warnings = []
        
        # Stage 1: Schema validation
        schema_valid, schema_issues = self._validate_schema(extracted)
        all_issues.extend(schema_issues)
        
        # Only run stage 2 if stage 1 passes
        semantic_valid = False
        if schema_valid:
            semantic_valid, semantic_issues = self._validate_semantic(extracted)
            all_issues.extend(semantic_issues)
            
            # Check duplicates if requested
            if check_duplicates:
                duplicate_issues = await self._check_duplicates(extracted)
                all_issues.extend(duplicate_issues)
        
        # Collect warnings
        for issue in all_issues:
            if issue.severity == "warning":
                warnings.append(issue.message)
        
        # Overall validity
        is_valid = schema_valid and semantic_valid
        
        # Can proceed with review if we have minimum required fields
        # even if there are warnings
        can_proceed = (
            extracted.total_amount is not None
            and not any(
                issue.severity == "error" and issue.field != "vendor"
                for issue in all_issues
            )
        )
        
        return ValidationResult(
            extraction_id=extracted.extraction_id,
            schema_valid=schema_valid,
            semantic_valid=semantic_valid,
            is_valid=is_valid,
            can_proceed_with_review=can_proceed,
            issues=all_issues,
            warnings=warnings,
        )
    
    def get_user_friendly_summary(
        self,
        result: ValidationResult,
    ) -> str:
        """
        Generate a user-friendly summary of validation results.
        
        This is what we show to non-technical users.
        """
        if result.is_valid and not result.warnings:
            return "✅ All checks passed! Please review the details below."
        
        lines = []
        
        if not result.schema_valid:
            lines.append("❌ Some required information could not be extracted:")
            for issue in result.issues:
                if issue.severity == "error":
                    lines.append(f"   • {issue.message}")
                    if issue.suggested_fix:
                        lines.append(f"     💡 {issue.suggested_fix}")
        
        if result.warnings:
            lines.append("")
            lines.append("⚠️ Please verify the following:")
            for warning in result.warnings:
                lines.append(f"   • {warning}")
        
        if result.can_proceed_with_review:
            lines.append("")
            lines.append("You can still proceed, but please review carefully.")
        else:
            lines.append("")
            lines.append("Please fix the issues above before continuing.")
        
        return "\n".join(lines)
