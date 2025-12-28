"""
OCR Service using Mindee

DESIGN DECISION: We use Mindee because:
1. Specialized for financial documents (invoices, receipts, bills)
2. Returns STRUCTURED data, not just raw text
3. Provides confidence scores
4. Handles Indian bill formats reasonably well

This service handles:
1. Sending enhanced images to Mindee
2. Parsing structured response
3. Document type validation (ONLY accept invoices/bills)
4. Converting Mindee response to our ExtractedBillData model

CRITICAL: We REJECT any document that isn't classified as an invoice/bill.
We do NOT try to extract data from random documents.
"""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Optional
from uuid import UUID

from mindee import Client, AsyncPredictResponse
from mindee.product import InvoiceV4
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.models.bill import (
    BillCategory,
    BillLineItem,
    DocumentType,
    ExtractedBillData,
    VendorInfo,
)


class OCRError(Exception):
    """Base exception for OCR errors."""
    pass


class DocumentTypeRejectedError(OCRError):
    """Document type is not acceptable (not a bill/invoice)."""
    
    def __init__(self, detected_type: str, message: str):
        self.detected_type = detected_type
        super().__init__(message)


class ExtractionFailedError(OCRError):
    """Failed to extract data from document."""
    pass


class MindeeOCRService:
    """
    OCR service using Mindee for structured document extraction.
    
    IMPORTANT BOUNDARIES:
    1. This service ONLY extracts data - it does NOT validate semantically
    2. This service REJECTS non-invoice documents loudly
    3. Confidence scores are preserved for downstream validation
    """
    
    def __init__(self):
        self._settings = get_settings().mindee
        self._app_settings = get_settings().app
        self._client: Optional[Client] = None
    
    def _get_client(self) -> Client:
        """Get or create Mindee client."""
        if self._client is None:
            self._client = Client(api_key=self._settings.api_key)
        return self._client
    
    def _safe_decimal(self, value) -> Optional[Decimal]:
        """Safely convert a value to Decimal."""
        if value is None:
            return None
        try:
            # Mindee returns float/None
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError):
            return None
    
    def _safe_date(self, value) -> Optional[date]:
        """Safely convert a value to date."""
        if value is None:
            return None
        try:
            if isinstance(value, date):
                return value
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, str):
                # Try common formats
                for fmt in ["%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"]:
                    try:
                        return datetime.strptime(value, fmt).date()
                    except ValueError:
                        continue
            return None
        except Exception:
            return None
    
    def _guess_category(self, vendor_name: str, line_items: list) -> Optional[BillCategory]:
        """
        Make an educated guess about bill category based on vendor/items.
        
        This is a SUGGESTION only - user must confirm.
        
        DESIGN DECISION: We use simple keyword matching rather than ML because:
        1. More transparent to user
        2. Easier to debug
        3. User confirms anyway
        """
        if not vendor_name:
            return None
        
        vendor_lower = vendor_name.lower()
        
        # Electricity keywords (Indian providers)
        electricity_keywords = [
            "electricity", "power", "discom", "bescom", "msedcl", 
            "tata power", "adani", "torrent", "reliance energy",
            "bses", "ndpl", "uppcl", "kseb", "tneb", "wbsedcl",
            "cesc", "dgvcl", "pgvcl", "mgvcl", "ugvcl",
        ]
        if any(kw in vendor_lower for kw in electricity_keywords):
            return BillCategory.ELECTRICITY
        
        # Water keywords
        water_keywords = [
            "water", "jal", "phed", "municipal", "nagar",
            "jalkal", "bwssb", "hmwssb",
        ]
        if any(kw in vendor_lower for kw in water_keywords):
            return BillCategory.WATER
        
        # Gas keywords  
        gas_keywords = [
            "gas", "indraprastha", "mahanagar", "gail",
            "adani gas", "gujarat gas", "igl", "mgl", "bgl",
            "hp gas", "bharat gas", "indane",
        ]
        if any(kw in vendor_lower for kw in gas_keywords):
            return BillCategory.GAS
        
        # Internet/broadband keywords
        internet_keywords = [
            "internet", "broadband", "fiber", "airtel", "jio",
            "act fibernet", "hathway", "you broadband", "bsnl",
            "tata sky", "tikona", "spectra",
        ]
        if any(kw in vendor_lower for kw in internet_keywords):
            return BillCategory.INTERNET
        
        # Mobile keywords
        mobile_keywords = [
            "mobile", "prepaid", "postpaid", "recharge",
            "vodafone", "idea", "vi ", "airtel", "jio", "bsnl",
        ]
        if any(kw in vendor_lower for kw in mobile_keywords):
            return BillCategory.MOBILE
        
        # Medical keywords
        medical_keywords = [
            "hospital", "clinic", "pharmacy", "medical", "diagnostic",
            "lab", "pathology", "doctor", "health",
            "apollo", "fortis", "max", "medanta", "aiims",
        ]
        if any(kw in vendor_lower for kw in medical_keywords):
            return BillCategory.MEDICAL
        
        # Insurance keywords
        insurance_keywords = [
            "insurance", "lic", "hdfc life", "icici prudential",
            "sbi life", "max life", "bajaj allianz", "policy",
        ]
        if any(kw in vendor_lower for kw in insurance_keywords):
            return BillCategory.INSURANCE
        
        # Grocery keywords
        grocery_keywords = [
            "grocery", "supermarket", "mart", "store", "kirana",
            "big bazaar", "dmart", "reliance fresh", "more",
            "spencer", "star bazaar",
        ]
        if any(kw in vendor_lower for kw in grocery_keywords):
            return BillCategory.GROCERIES
        
        # Fuel keywords
        fuel_keywords = [
            "petrol", "diesel", "fuel", "petroleum",
            "indian oil", "bharat petroleum", "hp ", "iocl", "bpcl", "hpcl",
            "shell", "essar", "reliance petrol",
        ]
        if any(kw in vendor_lower for kw in fuel_keywords):
            return BillCategory.FUEL
        
        return BillCategory.OTHER
    
    def _extract_line_items(self, mindee_items) -> list[BillLineItem]:
        """Extract line items from Mindee response."""
        items = []
        
        if not mindee_items:
            return items
        
        for item in mindee_items:
            try:
                # Mindee InvoiceV4 line item fields
                description = getattr(item, 'description', None) or "Item"
                amount = self._safe_decimal(getattr(item, 'total_amount', None))
                quantity = self._safe_decimal(getattr(item, 'quantity', None))
                unit = getattr(item, 'unit_measure', None)
                
                if amount is not None:
                    items.append(BillLineItem(
                        description=str(description)[:200],
                        amount=amount,
                        quantity=quantity,
                        unit=str(unit)[:20] if unit else None,
                    ))
            except Exception:
                # Skip malformed items
                continue
        
        return items
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def extract_bill_data(
        self,
        image_url: str,
        upload_id: UUID,
    ) -> ExtractedBillData:
        """
        Extract structured bill data from an image using Mindee.
        
        Args:
            image_url: URL to the enhanced image (Cloudinary)
            upload_id: ID of the original upload (for correlation)
            
        Returns:
            ExtractedBillData with extracted fields and confidence
            
        Raises:
            DocumentTypeRejectedError: If document is not a bill/invoice
            ExtractionFailedError: If extraction completely fails
        """
        client = self._get_client()
        
        try:
            # Use Mindee Invoice API
            # This works for invoices and utility bills
            result: AsyncPredictResponse = client.parse(
                InvoiceV4,
                image_url,
            )
            
            prediction = result.document.inference.prediction
            
            # Check document type confidence
            # Mindee doesn't explicitly classify document type,
            # but low confidence on key fields suggests it's not an invoice
            
            # Get overall confidence from key fields
            confidences = []
            
            if hasattr(prediction, 'total_amount') and prediction.total_amount.value is not None:
                confidences.append(prediction.total_amount.confidence)
            
            if hasattr(prediction, 'date') and prediction.date.value is not None:
                confidences.append(prediction.date.confidence)
            
            if hasattr(prediction, 'supplier_name') and prediction.supplier_name.value is not None:
                confidences.append(prediction.supplier_name.confidence)
            
            # Calculate overall confidence
            if confidences:
                overall_confidence = sum(confidences) / len(confidences)
            else:
                overall_confidence = 0.0
            
            # If no key fields extracted, reject
            if overall_confidence < 0.2:
                raise DocumentTypeRejectedError(
                    detected_type="unknown",
                    message=(
                        "This doesn't appear to be a bill or invoice. "
                        "Please upload a clear photo of a bill, receipt, or invoice."
                    )
                )
            
            # Determine document type
            # Mindee Invoice API accepts invoices, receipts, and bills
            if overall_confidence >= self._app_settings.min_ocr_confidence:
                doc_type = DocumentType.INVOICE
            elif overall_confidence >= 0.4:
                doc_type = DocumentType.RECEIPT
            else:
                doc_type = DocumentType.UNKNOWN
            
            # Extract vendor info
            vendor = None
            vendor_name = None
            if prediction.supplier_name.value:
                vendor_name = prediction.supplier_name.value
                vendor = VendorInfo(
                    name=vendor_name[:200],
                    address=prediction.supplier_address.value if hasattr(prediction, 'supplier_address') else None,
                )
            
            # Extract dates
            bill_date = self._safe_date(
                prediction.date.value if hasattr(prediction, 'date') else None
            )
            due_date = self._safe_date(
                prediction.due_date.value if hasattr(prediction, 'due_date') else None
            )
            
            # Extract amounts
            total_amount = self._safe_decimal(
                prediction.total_amount.value if hasattr(prediction, 'total_amount') else None
            )
            tax_amount = self._safe_decimal(
                prediction.total_tax.value if hasattr(prediction, 'total_tax') else None
            )
            subtotal = self._safe_decimal(
                prediction.total_net.value if hasattr(prediction, 'total_net') else None
            )
            
            # Extract line items
            line_items = self._extract_line_items(
                prediction.line_items if hasattr(prediction, 'line_items') else []
            )
            
            # Extract invoice/bill number
            bill_number = None
            if hasattr(prediction, 'invoice_number') and prediction.invoice_number.value:
                bill_number = str(prediction.invoice_number.value)[:50]
            
            # Guess category
            suggested_category = self._guess_category(
                vendor_name or "",
                line_items,
            )
            
            # Build raw OCR text for debugging
            raw_text_parts = []
            if vendor_name:
                raw_text_parts.append(f"Vendor: {vendor_name}")
            if bill_number:
                raw_text_parts.append(f"Bill#: {bill_number}")
            if bill_date:
                raw_text_parts.append(f"Date: {bill_date}")
            if total_amount:
                raw_text_parts.append(f"Total: ₹{total_amount}")
            
            raw_ocr_text = "\n".join(raw_text_parts) if raw_text_parts else None
            
            return ExtractedBillData(
                extracted_at=datetime.utcnow(),
                confidence_score=overall_confidence,
                document_type=doc_type,
                vendor=vendor,
                bill_number=bill_number,
                bill_date=bill_date,
                due_date=due_date,
                subtotal=subtotal,
                tax_amount=tax_amount,
                total_amount=total_amount,
                line_items=line_items,
                suggested_category=suggested_category,
                raw_ocr_text=raw_ocr_text,
            )
            
        except DocumentTypeRejectedError:
            raise
        except Exception as e:
            raise ExtractionFailedError(f"Failed to extract bill data: {e}")
    
    def should_proceed_with_extraction(
        self,
        extracted: ExtractedBillData,
    ) -> tuple[bool, str]:
        """
        Determine if extraction quality is good enough to proceed.
        
        Returns: (should_proceed, message_for_user)
        """
        min_confidence = self._app_settings.min_ocr_confidence
        
        if extracted.document_type == DocumentType.UNKNOWN:
            return False, (
                "❌ Could not identify this as a bill or invoice. "
                "Please upload a clearer photo of your bill."
            )
        
        if extracted.confidence_score < 0.3:
            return False, (
                "❌ Extraction confidence is too low. "
                "The image may be too blurry or the document format is not supported. "
                "Please try again with a clearer photo."
            )
        
        if extracted.total_amount is None:
            return False, (
                "⚠️ Could not extract the total amount from this bill. "
                "Please ensure the total is clearly visible in the photo."
            )
        
        if extracted.confidence_score < min_confidence:
            # Proceed but warn
            return True, (
                f"⚠️ Extraction confidence ({extracted.confidence_score:.0%}) is below ideal. "
                "Please review the extracted data carefully."
            )
        
        return True, "✅ Bill data extracted successfully. Please review and confirm."
