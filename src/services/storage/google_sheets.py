"""
Google Sheets Storage Implementation

DESIGN DECISION: Google Sheets is used as the initial storage backend because:
1. Non-technical users can view their data directly in Sheets
2. No database setup required
3. Built-in backup (Google's infrastructure)
4. Easy to export/migrate later

TRADEOFFS:
- Not suitable for high-volume data (we're fine for personal use)
- No transactions (we handle this with careful ordering)
- Limited query capabilities (we filter in Python)

The implementation follows the abstract interface, so we can swap
to PostgreSQL/SQLite later without changing business logic.
"""

import json
from datetime import date, datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

import gspread
from google.oauth2.service_account import Credentials
from tenacity import retry, stop_after_attempt, wait_exponential

from src.config import get_settings
from src.models.bill import (
    BillCategory,
    BillLineItem,
    BillStatus,
    ConfirmedBill,
    PaymentStatus,
    VendorInfo,
)
from src.models.audit import AuditEvent, AuditEventType, AuditSeverity
from src.services.storage.interface import (
    AuditStorageInterface,
    BillStorageInterface,
    ConnectionError,
    DuplicateError,
    NotFoundError,
    StorageError,
)


# Column mappings for Bills sheet
BILL_COLUMNS = [
    "id",
    "extraction_id", 
    "created_at",
    "updated_at",
    "vendor_name",
    "category",
    "total_amount",
    "bill_date",
    "bill_number",
    "due_date",
    "billing_period_start",
    "billing_period_end",
    "subtotal",
    "tax_amount",
    "status",
    "payment_status",
    "paid_date",
    "notes",
    "original_image_url",
    "enhanced_image_url",
    "vendor_info_json",
    "line_items_json",
]

# Column mappings for Audit sheet
AUDIT_COLUMNS = [
    "event_id",
    "timestamp",
    "event_type",
    "severity",
    "entity_type",
    "entity_id",
    "correlation_id",
    "description",
    "details_json",
    "error_message",
    "is_user_action",
]


class GoogleSheetsClient:
    """
    Low-level Google Sheets client wrapper.
    
    Handles authentication and provides retry logic for API calls.
    """
    
    def __init__(self):
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None
        self._settings = get_settings().google_sheets
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def connect(self) -> gspread.Client:
        """
        Establish connection to Google Sheets.
        
        Uses service account credentials for authentication.
        """
        if self._client is None:
            try:
                scopes = [
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ]
                credentials = Credentials.from_service_account_file(
                    self._settings.credentials_path,
                    scopes=scopes,
                )
                self._client = gspread.authorize(credentials)
            except FileNotFoundError:
                raise ConnectionError(
                    f"Google credentials file not found: {self._settings.credentials_path}"
                )
            except Exception as e:
                raise ConnectionError(f"Failed to connect to Google Sheets: {e}")
        
        return self._client
    
    def get_spreadsheet(self) -> gspread.Spreadsheet:
        """Get the configured spreadsheet."""
        if self._spreadsheet is None:
            client = self.connect()
            try:
                self._spreadsheet = client.open_by_key(
                    self._settings.spreadsheet_id
                )
            except gspread.SpreadsheetNotFound:
                raise ConnectionError(
                    f"Spreadsheet not found: {self._settings.spreadsheet_id}"
                )
        return self._spreadsheet
    
    def get_bills_sheet(self) -> gspread.Worksheet:
        """Get or create the Bills worksheet."""
        spreadsheet = self.get_spreadsheet()
        try:
            sheet = spreadsheet.worksheet(self._settings.bills_sheet_name)
        except gspread.WorksheetNotFound:
            # Create the sheet with headers
            sheet = spreadsheet.add_worksheet(
                title=self._settings.bills_sheet_name,
                rows=1000,
                cols=len(BILL_COLUMNS),
            )
            sheet.append_row(BILL_COLUMNS)
        return sheet
    
    def get_audit_sheet(self) -> gspread.Worksheet:
        """Get or create the Audit worksheet."""
        spreadsheet = self.get_spreadsheet()
        try:
            sheet = spreadsheet.worksheet(self._settings.audit_sheet_name)
        except gspread.WorksheetNotFound:
            # Create the sheet with headers
            sheet = spreadsheet.add_worksheet(
                title=self._settings.audit_sheet_name,
                rows=5000,  # More rows for audit log
                cols=len(AUDIT_COLUMNS),
            )
            sheet.append_row(AUDIT_COLUMNS)
        return sheet


class GoogleSheetsBillStorage(BillStorageInterface):
    """
    Google Sheets implementation of bill storage.
    
    Bills are stored as rows in a worksheet with one bill per row.
    Complex fields (vendor_info, line_items) are JSON-serialized.
    """
    
    def __init__(self, client: Optional[GoogleSheetsClient] = None):
        self._client = client or GoogleSheetsClient()
    
    def _bill_to_row(self, bill: ConfirmedBill) -> list:
        """Convert a ConfirmedBill to a spreadsheet row."""
        return [
            str(bill.id),
            str(bill.extraction_id),
            bill.created_at.isoformat(),
            bill.updated_at.isoformat(),
            bill.vendor_name,
            bill.category.value,
            str(bill.total_amount),
            bill.bill_date.isoformat(),
            bill.bill_number or "",
            bill.due_date.isoformat() if bill.due_date else "",
            bill.billing_period_start.isoformat() if bill.billing_period_start else "",
            bill.billing_period_end.isoformat() if bill.billing_period_end else "",
            str(bill.subtotal) if bill.subtotal else "",
            str(bill.tax_amount) if bill.tax_amount else "",
            bill.status.value,
            bill.payment_status.value,
            bill.paid_date.isoformat() if bill.paid_date else "",
            bill.notes or "",
            bill.original_image_url or "",
            bill.enhanced_image_url or "",
            json.dumps(bill.vendor_info.model_dump() if bill.vendor_info else {}),
            json.dumps([item.model_dump() for item in bill.line_items]),
        ]
    
    def _row_to_bill(self, row: list) -> ConfirmedBill:
        """Convert a spreadsheet row to a ConfirmedBill."""
        # Handle missing columns gracefully
        def safe_get(index: int, default: str = "") -> str:
            try:
                return row[index] if row[index] else default
            except IndexError:
                return default
        
        # Parse vendor info
        vendor_info = None
        vendor_json = safe_get(20)
        if vendor_json:
            vendor_data = json.loads(vendor_json)
            if vendor_data:
                vendor_info = VendorInfo(**vendor_data)
        
        # Parse line items
        line_items = []
        items_json = safe_get(21)
        if items_json:
            items_data = json.loads(items_json)
            line_items = [BillLineItem(**item) for item in items_data]
        
        return ConfirmedBill(
            id=UUID(safe_get(0)),
            extraction_id=UUID(safe_get(1)),
            created_at=datetime.fromisoformat(safe_get(2)),
            updated_at=datetime.fromisoformat(safe_get(3)),
            vendor_name=safe_get(4),
            category=BillCategory(safe_get(5)),
            total_amount=Decimal(safe_get(6)),
            bill_date=date.fromisoformat(safe_get(7)),
            bill_number=safe_get(8) or None,
            due_date=date.fromisoformat(safe_get(9)) if safe_get(9) else None,
            billing_period_start=date.fromisoformat(safe_get(10)) if safe_get(10) else None,
            billing_period_end=date.fromisoformat(safe_get(11)) if safe_get(11) else None,
            subtotal=Decimal(safe_get(12)) if safe_get(12) else None,
            tax_amount=Decimal(safe_get(13)) if safe_get(13) else None,
            status=BillStatus(safe_get(14)),
            payment_status=PaymentStatus(safe_get(15)),
            paid_date=date.fromisoformat(safe_get(16)) if safe_get(16) else None,
            notes=safe_get(17) or None,
            original_image_url=safe_get(18) or None,
            enhanced_image_url=safe_get(19) or None,
            vendor_info=vendor_info,
            line_items=line_items,
        )
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def save_bill(self, bill: ConfirmedBill) -> bool:
        """Save a confirmed bill to Google Sheets."""
        try:
            sheet = self._client.get_bills_sheet()
            row = self._bill_to_row(bill)
            sheet.append_row(row, value_input_option="RAW")
            return True
        except Exception as e:
            raise StorageError(f"Failed to save bill: {e}")
    
    async def get_bill_by_id(self, bill_id: UUID) -> Optional[ConfirmedBill]:
        """Retrieve a bill by its ID."""
        try:
            sheet = self._client.get_bills_sheet()
            # Get all data (excluding header)
            all_rows = sheet.get_all_values()[1:]
            
            for row in all_rows:
                if row and row[0] == str(bill_id):
                    return self._row_to_bill(row)
            
            return None
        except Exception as e:
            raise StorageError(f"Failed to get bill: {e}")
    
    async def update_bill(self, bill: ConfirmedBill) -> bool:
        """Update an existing bill."""
        try:
            sheet = self._client.get_bills_sheet()
            all_rows = sheet.get_all_values()
            
            # Find the row with this bill ID
            for idx, row in enumerate(all_rows[1:], start=2):  # Start from 2 (row 1 is header)
                if row and row[0] == str(bill.id):
                    # Update the row
                    bill.updated_at = datetime.utcnow()
                    new_row = self._bill_to_row(bill)
                    
                    # Update each cell in the row
                    for col_idx, value in enumerate(new_row, start=1):
                        sheet.update_cell(idx, col_idx, value)
                    
                    return True
            
            raise NotFoundError(f"Bill not found: {bill.id}")
        except NotFoundError:
            raise
        except Exception as e:
            raise StorageError(f"Failed to update bill: {e}")
    
    async def delete_bill(self, bill_id: UUID) -> bool:
        """Delete a bill by ID."""
        try:
            sheet = self._client.get_bills_sheet()
            all_rows = sheet.get_all_values()
            
            for idx, row in enumerate(all_rows[1:], start=2):
                if row and row[0] == str(bill_id):
                    sheet.delete_rows(idx)
                    return True
            
            return False
        except Exception as e:
            raise StorageError(f"Failed to delete bill: {e}")
    
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
        """List bills with optional filters."""
        try:
            sheet = self._client.get_bills_sheet()
            all_rows = sheet.get_all_values()[1:]  # Skip header
            
            bills = []
            for row in all_rows:
                if not row or not row[0]:  # Skip empty rows
                    continue
                
                try:
                    bill = self._row_to_bill(row)
                except Exception:
                    continue  # Skip malformed rows
                
                # Apply filters
                if category and bill.category != category:
                    continue
                if vendor and vendor.lower() not in bill.vendor_name.lower():
                    continue
                if date_from and bill.bill_date < date_from:
                    continue
                if date_to and bill.bill_date > date_to:
                    continue
                if payment_status and bill.payment_status != payment_status:
                    continue
                
                bills.append(bill)
            
            # Sort by date descending (newest first)
            bills.sort(key=lambda b: b.bill_date, reverse=True)
            
            # Apply pagination
            return bills[offset:offset + limit]
        except Exception as e:
            raise StorageError(f"Failed to list bills: {e}")
    
    async def get_total_by_category(
        self,
        category: BillCategory,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> float:
        """Get total amount for a category."""
        bills = await self.list_bills(
            category=category,
            date_from=date_from,
            date_to=date_to,
            limit=1000,
        )
        return sum(float(bill.total_amount) for bill in bills)
    
    async def get_total_by_vendor(
        self,
        vendor: str,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
    ) -> float:
        """Get total amount for a vendor."""
        bills = await self.list_bills(
            vendor=vendor,
            date_from=date_from,
            date_to=date_to,
            limit=1000,
        )
        return sum(float(bill.total_amount) for bill in bills)
    
    async def bill_exists(
        self,
        vendor: str,
        bill_number: Optional[str],
        bill_date: date,
    ) -> bool:
        """Check if a similar bill already exists."""
        bills = await self.list_bills(
            vendor=vendor,
            date_from=bill_date,
            date_to=bill_date,
            limit=100,
        )
        
        for bill in bills:
            # If bill numbers match, it's a duplicate
            if bill_number and bill.bill_number == bill_number:
                return True
            # If same vendor, same date, same amount... probably a duplicate
            # But we only check vendor/date here for safety
        
        # Only consider it a duplicate if bill number matches
        return False


class GoogleSheetsAuditStorage(AuditStorageInterface):
    """
    Google Sheets implementation of audit log storage.
    
    Audit events are append-only.
    """
    
    def __init__(self, client: Optional[GoogleSheetsClient] = None):
        self._client = client or GoogleSheetsClient()
    
    def _event_to_row(self, event: AuditEvent) -> list:
        """Convert an AuditEvent to a spreadsheet row."""
        return [
            str(event.event_id),
            event.timestamp.isoformat(),
            event.event_type.value,
            event.severity.value,
            event.entity_type or "",
            str(event.entity_id) if event.entity_id else "",
            str(event.correlation_id) if event.correlation_id else "",
            event.description,
            json.dumps(event.details) if event.details else "",
            event.error_message or "",
            str(event.is_user_action),
        ]
    
    def _row_to_event(self, row: list) -> AuditEvent:
        """Convert a spreadsheet row to an AuditEvent."""
        def safe_get(index: int, default: str = "") -> str:
            try:
                return row[index] if row[index] else default
            except IndexError:
                return default
        
        return AuditEvent(
            event_id=UUID(safe_get(0)),
            timestamp=datetime.fromisoformat(safe_get(1)),
            event_type=AuditEventType(safe_get(2)),
            severity=AuditSeverity(safe_get(3)),
            entity_type=safe_get(4) or None,
            entity_id=UUID(safe_get(5)) if safe_get(5) else None,
            correlation_id=UUID(safe_get(6)) if safe_get(6) else None,
            description=safe_get(7),
            details=json.loads(safe_get(8)) if safe_get(8) else {},
            error_message=safe_get(9) or None,
            is_user_action=safe_get(10).lower() == "true",
        )
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def append_event(self, event: AuditEvent) -> bool:
        """Append an audit event."""
        try:
            sheet = self._client.get_audit_sheet()
            row = self._event_to_row(event)
            sheet.append_row(row, value_input_option="RAW")
            return True
        except Exception as e:
            # Don't raise - audit logging should not break the main flow
            # But we should log this somehow (to file/console)
            print(f"WARNING: Failed to write audit event: {e}")
            return False
    
    async def get_events_by_correlation_id(
        self,
        correlation_id: UUID,
    ) -> list[AuditEvent]:
        """Get events by correlation ID."""
        try:
            sheet = self._client.get_audit_sheet()
            all_rows = sheet.get_all_values()[1:]
            
            events = []
            for row in all_rows:
                if row and len(row) > 6 and row[6] == str(correlation_id):
                    try:
                        events.append(self._row_to_event(row))
                    except Exception:
                        continue
            
            # Sort chronologically
            events.sort(key=lambda e: e.timestamp)
            return events
        except Exception as e:
            raise StorageError(f"Failed to get audit events: {e}")
    
    async def get_events_by_entity(
        self,
        entity_type: str,
        entity_id: UUID,
    ) -> list[AuditEvent]:
        """Get events by entity."""
        try:
            sheet = self._client.get_audit_sheet()
            all_rows = sheet.get_all_values()[1:]
            
            events = []
            for row in all_rows:
                if (
                    row
                    and len(row) > 5
                    and row[4] == entity_type
                    and row[5] == str(entity_id)
                ):
                    try:
                        events.append(self._row_to_event(row))
                    except Exception:
                        continue
            
            events.sort(key=lambda e: e.timestamp)
            return events
        except Exception as e:
            raise StorageError(f"Failed to get audit events: {e}")
    
    async def get_recent_events(
        self,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Get recent events."""
        try:
            sheet = self._client.get_audit_sheet()
            all_rows = sheet.get_all_values()[1:]
            
            events = []
            for row in all_rows:
                if row and row[0]:
                    try:
                        events.append(self._row_to_event(row))
                    except Exception:
                        continue
            
            # Sort newest first
            events.sort(key=lambda e: e.timestamp, reverse=True)
            return events[:limit]
        except Exception as e:
            raise StorageError(f"Failed to get audit events: {e}")
