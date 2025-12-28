"""
Query Execution Engine

DESIGN DECISION: Query execution is DETERMINISTIC.
The LLM converts natural language to StructuredQuery.
This engine executes that query on actual stored data.
The LLM then formats the response.

At no point does the LLM have direct access to answer questions.
It can only see what this engine returns from storage.

This is the critical boundary that prevents hallucination.
"""

from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from src.models.bill import (
    BillCategory,
    ConfirmedBill,
    PaymentStatus,
    QueryResult,
    StructuredQuery,
)
from src.services.storage import BillStorageInterface


class QueryExecutionError(Exception):
    """Error during query execution."""
    pass


class QueryExecutor:
    """
    Executes structured queries against bill storage.
    
    This is the bridge between:
    - AI-generated queries (from natural language)
    - Actual stored data
    
    GUARANTEES:
    - Only returns real data from storage
    - Never invents or estimates
    - Clear "no data found" if nothing matches
    """
    
    def __init__(self, storage: BillStorageInterface):
        self._storage = storage
    
    async def execute(self, query: StructuredQuery) -> QueryResult:
        """
        Execute a structured query and return results.
        
        The result will be used by the AI agent to generate
        a natural language response.
        """
        try:
            # Route to appropriate handler based on query type
            if query.query_type == "lookup":
                return await self._execute_lookup(query)
            elif query.query_type == "list":
                return await self._execute_list(query)
            elif query.query_type == "aggregate":
                return await self._execute_aggregate(query)
            elif query.query_type == "exists":
                return await self._execute_exists(query)
            elif query.query_type == "compare":
                return await self._execute_compare(query)
            else:
                # Default to list
                return await self._execute_list(query)
                
        except Exception as e:
            return QueryResult(
                query_id=query.query_id,
                success=False,
                error_message=str(e),
                data_found=False,
                result_count=0,
                query_description=f"Query failed: {str(e)}",
            )
    
    async def _execute_lookup(self, query: StructuredQuery) -> QueryResult:
        """Execute a lookup query (find specific bill(s))."""
        bills = await self._storage.list_bills(
            category=query.category_filter,
            vendor=query.vendor_filter,
            date_from=query.date_from,
            date_to=query.date_to,
            payment_status=query.payment_status_filter,
            limit=query.limit,
        )
        
        results = [self._bill_to_dict(bill) for bill in bills]
        
        # Build description
        desc_parts = ["Looking for bills"]
        if query.category_filter:
            desc_parts.append(f"category: {query.category_filter.value}")
        if query.vendor_filter:
            desc_parts.append(f"vendor: {query.vendor_filter}")
        if query.date_from or query.date_to:
            date_str = self._date_range_str(query.date_from, query.date_to)
            desc_parts.append(date_str)
        
        return QueryResult(
            query_id=query.query_id,
            success=True,
            data_found=len(results) > 0,
            result_count=len(results),
            results=results,
            query_description=" | ".join(desc_parts),
        )
    
    async def _execute_list(self, query: StructuredQuery) -> QueryResult:
        """Execute a list query."""
        bills = await self._storage.list_bills(
            category=query.category_filter,
            vendor=query.vendor_filter,
            date_from=query.date_from,
            date_to=query.date_to,
            payment_status=query.payment_status_filter,
            limit=query.limit,
        )
        
        results = [self._bill_to_dict(bill) for bill in bills]
        
        # Build description
        desc_parts = ["Listing bills"]
        if query.category_filter:
            desc_parts.append(f"category: {query.category_filter.value}")
        if query.vendor_filter:
            desc_parts.append(f"vendor: {query.vendor_filter}")
        if query.payment_status_filter:
            desc_parts.append(f"status: {query.payment_status_filter.value}")
        if query.date_from or query.date_to:
            date_str = self._date_range_str(query.date_from, query.date_to)
            desc_parts.append(date_str)
        
        return QueryResult(
            query_id=query.query_id,
            success=True,
            data_found=len(results) > 0,
            result_count=len(results),
            results=results,
            query_description=" | ".join(desc_parts),
        )
    
    async def _execute_aggregate(self, query: StructuredQuery) -> QueryResult:
        """Execute an aggregate query (sum, count, average, etc.)."""
        bills = await self._storage.list_bills(
            category=query.category_filter,
            vendor=query.vendor_filter,
            date_from=query.date_from,
            date_to=query.date_to,
            payment_status=query.payment_status_filter,
            limit=1000,  # Get all for aggregation
        )
        
        if not bills:
            return QueryResult(
                query_id=query.query_id,
                success=True,
                data_found=False,
                result_count=0,
                query_description="No bills found for aggregation",
            )
        
        # Calculate aggregation
        amounts = [float(bill.total_amount) for bill in bills]
        
        aggregation_result = {}
        
        if query.aggregation_type == "sum" or query.aggregation_type is None:
            aggregation_result["total_amount"] = sum(amounts)
            aggregation_result["bill_count"] = len(amounts)
        elif query.aggregation_type == "count":
            aggregation_result["count"] = len(amounts)
        elif query.aggregation_type == "average":
            aggregation_result["average_amount"] = sum(amounts) / len(amounts)
            aggregation_result["bill_count"] = len(amounts)
        elif query.aggregation_type == "min":
            aggregation_result["minimum_amount"] = min(amounts)
        elif query.aggregation_type == "max":
            aggregation_result["maximum_amount"] = max(amounts)
        
        # Handle grouping
        if query.group_by:
            grouped = await self._execute_grouped_aggregate(
                bills, query.group_by, query.aggregation_type
            )
            aggregation_result["breakdown"] = grouped
        
        # Build description
        desc_parts = []
        if query.aggregation_type:
            desc_parts.append(f"Calculating {query.aggregation_type}")
        else:
            desc_parts.append("Calculating total")
        if query.category_filter:
            desc_parts.append(f"for {query.category_filter.value}")
        if query.vendor_filter:
            desc_parts.append(f"from {query.vendor_filter}")
        if query.date_from or query.date_to:
            desc_parts.append(self._date_range_str(query.date_from, query.date_to))
        if query.group_by:
            desc_parts.append(f"grouped by {query.group_by}")
        
        return QueryResult(
            query_id=query.query_id,
            success=True,
            data_found=True,
            result_count=len(bills),
            aggregation_result=aggregation_result,
            query_description=" ".join(desc_parts),
        )
    
    async def _execute_grouped_aggregate(
        self,
        bills: list[ConfirmedBill],
        group_by: str,
        aggregation_type: Optional[str],
    ) -> dict:
        """Calculate aggregation grouped by a field."""
        groups = {}
        
        for bill in bills:
            # Determine group key
            if group_by == "category":
                key = bill.category.value
            elif group_by == "vendor":
                key = bill.vendor_name
            elif group_by == "month":
                key = bill.bill_date.strftime("%Y-%m")
            elif group_by == "year":
                key = str(bill.bill_date.year)
            else:
                key = "other"
            
            if key not in groups:
                groups[key] = []
            groups[key].append(float(bill.total_amount))
        
        # Calculate aggregation for each group
        result = {}
        for key, amounts in groups.items():
            if aggregation_type == "count":
                result[key] = len(amounts)
            elif aggregation_type == "average":
                result[key] = sum(amounts) / len(amounts)
            elif aggregation_type == "min":
                result[key] = min(amounts)
            elif aggregation_type == "max":
                result[key] = max(amounts)
            else:  # Default to sum
                result[key] = sum(amounts)
        
        return result
    
    async def _execute_exists(self, query: StructuredQuery) -> QueryResult:
        """Execute an exists query (yes/no check)."""
        bills = await self._storage.list_bills(
            category=query.category_filter,
            vendor=query.vendor_filter,
            date_from=query.date_from,
            date_to=query.date_to,
            payment_status=query.payment_status_filter,
            limit=1,  # We only need to know if any exist
        )
        
        exists = len(bills) > 0
        
        # Build description
        desc_parts = ["Checking if"]
        if query.category_filter:
            desc_parts.append(f"{query.category_filter.value} bill")
        else:
            desc_parts.append("bill")
        if query.payment_status_filter:
            desc_parts.append(f"was {query.payment_status_filter.value}")
        if query.vendor_filter:
            desc_parts.append(f"from {query.vendor_filter}")
        if query.date_from or query.date_to:
            desc_parts.append(self._date_range_str(query.date_from, query.date_to))
        
        result_data = [{"exists": exists, "answer": "yes" if exists else "no"}]
        if exists and bills:
            # Include the matching bill for context
            result_data.append(self._bill_to_dict(bills[0]))
        
        return QueryResult(
            query_id=query.query_id,
            success=True,
            data_found=exists,
            result_count=1 if exists else 0,
            results=result_data,
            query_description=" ".join(desc_parts),
        )
    
    async def _execute_compare(self, query: StructuredQuery) -> QueryResult:
        """Execute a comparison query."""
        # For now, treat as aggregation with grouping
        # More sophisticated comparison logic can be added later
        return await self._execute_aggregate(query)
    
    def _bill_to_dict(self, bill: ConfirmedBill) -> dict:
        """Convert a bill to a dictionary for results."""
        return {
            "id": str(bill.id),
            "vendor_name": bill.vendor_name,
            "category": bill.category.value,
            "total_amount": float(bill.total_amount),
            "bill_date": bill.bill_date.isoformat(),
            "due_date": bill.due_date.isoformat() if bill.due_date else None,
            "payment_status": bill.payment_status.value,
            "bill_number": bill.bill_number,
        }
    
    def _date_range_str(
        self,
        date_from: Optional[date],
        date_to: Optional[date],
    ) -> str:
        """Format date range for description."""
        if date_from and date_to:
            if date_from == date_to:
                return f"on {date_from.strftime('%d %b %Y')}"
            elif date_from.month == date_to.month and date_from.year == date_to.year:
                return f"in {date_from.strftime('%B %Y')}"
            elif date_from.year == date_to.year:
                return f"from {date_from.strftime('%b')} to {date_to.strftime('%b %Y')}"
            else:
                return f"from {date_from.strftime('%b %Y')} to {date_to.strftime('%b %Y')}"
        elif date_from:
            return f"from {date_from.strftime('%d %b %Y')}"
        elif date_to:
            return f"until {date_to.strftime('%d %b %Y')}"
        return ""
