"""
AI Agents for Personal Accountant

DESIGN DECISION: We use Pydantic AI for agent orchestration because:
1. Type-safe function definitions
2. Structured outputs via Pydantic models
3. Clear boundaries for AI behavior
4. Easy testing and validation

CRITICAL BOUNDARIES:

1. BILL UPLOAD AGENT:
   - CAN: Suggest category, help interpret OCR results
   - CANNOT: Persist data without human confirmation
   - CANNOT: Make assumptions about missing data

2. QUERY AGENT (RAG):
   - CAN: Convert natural language to structured queries
   - CAN: Generate natural language responses FROM DATA
   - CANNOT: Answer questions directly from knowledge
   - CANNOT: Invent or hallucinate data
   - MUST: Say "no data found" if query returns nothing

The LLM is a TRANSLATOR, not an ORACLE.
It converts between human language and structured operations.
It NEVER makes up financial data.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional
from uuid import UUID, uuid4

import google.generativeai as genai
from pydantic import BaseModel, Field

from src.config import get_settings
from src.models.bill import (
    BillCategory,
    ConfirmedBill,
    ExtractedBillData,
    PaymentStatus,
    QueryResult,
    StructuredQuery,
)


class CategorySuggestion(BaseModel):
    """AI's suggestion for bill category."""
    
    category: BillCategory
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class QueryIntent(BaseModel):
    """
    Parsed intent from a natural language question.
    
    This is what the LLM extracts from the user's question.
    It is then converted to a StructuredQuery for execution.
    """
    
    query_type: str = Field(
        description="Type: lookup, aggregate, compare, list, exists"
    )
    
    # What the user wants to know
    target_entity: str = Field(
        default="bill",
        description="What they're asking about: bill, total, count, etc."
    )
    
    # Filters extracted from question
    category: Optional[str] = Field(
        default=None,
        description="Bill category mentioned (electricity, water, etc.)"
    )
    vendor: Optional[str] = Field(
        default=None,
        description="Vendor/company mentioned"
    )
    time_reference: Optional[str] = Field(
        default=None,
        description="Time reference (last month, this year, etc.)"
    )
    payment_status: Optional[str] = Field(
        default=None,
        description="Payment status mentioned (paid, unpaid)"
    )
    
    # For aggregations
    aggregation: Optional[str] = Field(
        default=None,
        description="Aggregation type: sum, count, average, etc."
    )
    group_by: Optional[str] = Field(
        default=None,
        description="Group by: category, vendor, month, etc."
    )


class NaturalLanguageResponse(BaseModel):
    """
    AI-generated natural language response based on query results.
    
    The LLM generates this FROM the query results.
    It NEVER invents data - only formats what was found.
    """
    
    response: str = Field(
        description="Human-friendly response"
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in the response"
    )
    data_used: bool = Field(
        description="Whether actual data was used"
    )


class BillUploadAgent:
    """
    AI agent for the bill upload flow.
    
    RESPONSIBILITIES:
    - Suggest bill category based on extracted data
    - Help interpret ambiguous OCR results
    - Generate user-friendly summaries
    
    BOUNDARIES:
    - NEVER persists data
    - NEVER makes assumptions about missing fields
    - ALWAYS defers to user for confirmation
    """
    
    def __init__(self):
        self._settings = get_settings().gemini
        self._configure_genai()
    
    def _configure_genai(self):
        """Configure Google Generative AI."""
        genai.configure(api_key=self._settings.api_key)
        self._model = genai.GenerativeModel(
            model_name=self._settings.model_name,
            generation_config={
                "temperature": 0.1,  # Low temperature for consistency
                "max_output_tokens": 512,
            }
        )
    
    async def suggest_category(
        self,
        extracted: ExtractedBillData,
    ) -> CategorySuggestion:
        """
        Suggest a category for the extracted bill.
        
        Uses vendor name, line items, and any other context
        to make an educated suggestion.
        
        Returns a suggestion that the user can accept or override.
        """
        # Build context for the LLM
        context_parts = []
        
        if extracted.vendor and extracted.vendor.name:
            context_parts.append(f"Vendor: {extracted.vendor.name}")
        
        if extracted.line_items:
            items_str = ", ".join(
                item.description[:50] for item in extracted.line_items[:5]
            )
            context_parts.append(f"Line items: {items_str}")
        
        if extracted.raw_ocr_text:
            context_parts.append(f"Bill text: {extracted.raw_ocr_text[:200]}")
        
        context = "\n".join(context_parts) or "No context available"
        
        # Categories for reference
        categories = [cat.value for cat in BillCategory]
        
        prompt = f"""You are helping categorize a bill for a personal accounting app.

Based on the following information, suggest the most appropriate category.

Bill Information:
{context}

Available categories: {', '.join(categories)}

Important:
- This is for an Indian household
- Common bills: electricity (BESCOM, MSEDCL, etc.), water (BWSSB, etc.), gas (Indraprastha, HP Gas, etc.), internet (Airtel, Jio, etc.), mobile, groceries, medical, insurance, rent, maintenance, fuel

Respond with ONLY a JSON object in this exact format:
{{"category": "category_name", "confidence": 0.8, "reasoning": "brief explanation"}}

Be conservative - if unsure, use "other" category."""

        try:
            response = await self._model.generate_content_async(prompt)
            text = response.text.strip()
            
            # Parse JSON response
            import json
            # Find JSON in response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = text[start:end]
                data = json.loads(json_str)
                
                # Validate category
                category_str = data.get("category", "other").lower()
                try:
                    category = BillCategory(category_str)
                except ValueError:
                    category = BillCategory.OTHER
                
                return CategorySuggestion(
                    category=category,
                    confidence=float(data.get("confidence", 0.5)),
                    reasoning=data.get("reasoning", "Based on available information"),
                )
        except Exception as e:
            # Fallback to rule-based suggestion
            pass
        
        # Fallback: use the pre-computed suggestion
        if extracted.suggested_category:
            return CategorySuggestion(
                category=extracted.suggested_category,
                confidence=0.6,
                reasoning="Based on vendor name pattern matching",
            )
        
        return CategorySuggestion(
            category=BillCategory.OTHER,
            confidence=0.3,
            reasoning="Could not determine category - please select manually",
        )
    
    async def generate_summary(
        self,
        extracted: ExtractedBillData,
        category: BillCategory,
    ) -> str:
        """
        Generate a human-friendly summary of the extracted bill.
        
        This helps the user understand what was extracted
        before they confirm.
        """
        parts = []
        
        if extracted.vendor and extracted.vendor.name:
            parts.append(f"📋 **Bill from:** {extracted.vendor.name}")
        
        if extracted.bill_date:
            parts.append(f"📅 **Date:** {extracted.bill_date.strftime('%d %B %Y')}")
        
        if extracted.total_amount:
            parts.append(f"💰 **Amount:** ₹{extracted.total_amount:,.2f}")
        
        parts.append(f"🏷️ **Category:** {category.value.replace('_', ' ').title()}")
        
        if extracted.due_date:
            parts.append(f"⏰ **Due by:** {extracted.due_date.strftime('%d %B %Y')}")
        
        if extracted.bill_number:
            parts.append(f"🔢 **Bill #:** {extracted.bill_number}")
        
        if extracted.confidence_score:
            confidence_emoji = (
                "🟢" if extracted.confidence_score >= 0.8
                else "🟡" if extracted.confidence_score >= 0.6
                else "🔴"
            )
            parts.append(
                f"{confidence_emoji} **Extraction confidence:** "
                f"{extracted.confidence_score:.0%}"
            )
        
        return "\n".join(parts)


class QueryAgent:
    """
    AI agent for the RAG-based query system.
    
    CRITICAL BOUNDARIES:
    1. The LLM ONLY converts questions to structured queries
    2. The LLM ONLY generates responses FROM fetched data
    3. The LLM NEVER answers from its own knowledge
    4. If no data is found, it MUST say so explicitly
    
    FLOW:
    1. User asks question → LLM extracts intent
    2. Intent → StructuredQuery (deterministic conversion)
    3. StructuredQuery executes on storage (deterministic)
    4. Results → LLM generates natural language response
    
    The LLM is sandwiched between two deterministic steps.
    It cannot hallucinate because it only sees real data.
    """
    
    def __init__(self):
        self._settings = get_settings().gemini
        self._configure_genai()
    
    def _configure_genai(self):
        """Configure Google Generative AI."""
        genai.configure(api_key=self._settings.api_key)
        self._model = genai.GenerativeModel(
            model_name=self._settings.model_name,
            generation_config={
                "temperature": 0.1,  # Very low for consistency
                "max_output_tokens": 1024,
            }
        )
    
    async def parse_question(
        self,
        question: str,
    ) -> QueryIntent:
        """
        Parse a natural language question into a structured intent.
        
        This is the first LLM call - converting human language
        to a structured representation we can execute.
        """
        prompt = f"""You are parsing a question about personal bills and expenses.

Question: "{question}"

Extract the intent as a JSON object with these fields:
- query_type: one of [lookup, aggregate, compare, list, exists]
  - lookup: finding specific bill(s)
  - aggregate: calculating totals, averages, counts
  - compare: comparing periods or categories
  - list: listing bills matching criteria
  - exists: checking if something exists (yes/no)
  
- target_entity: what they want (bill, total, count, average, etc.)

- category: if they mention a bill type, map to one of:
  electricity, water, gas, internet, mobile, groceries, medical, 
  insurance, rent, maintenance, fuel, other
  
- vendor: if they mention a specific company/vendor

- time_reference: if they mention a time period like:
  "last month", "this month", "this year", "January", "2024", etc.
  
- payment_status: if they mention paid/unpaid status

- aggregation: for aggregate queries: sum, count, average, min, max

- group_by: if they want breakdown: category, vendor, month, year

Examples:
"Did I pay electricity bill last month?" → 
{{"query_type": "exists", "target_entity": "bill", "category": "electricity", "time_reference": "last month", "payment_status": "paid"}}

"How much did I spend on groceries this year?" →
{{"query_type": "aggregate", "target_entity": "total", "category": "groceries", "time_reference": "this year", "aggregation": "sum"}}

"List all unpaid bills" →
{{"query_type": "list", "target_entity": "bill", "payment_status": "unpaid"}}

Respond with ONLY the JSON object, no explanation."""

        try:
            response = await self._model.generate_content_async(prompt)
            text = response.text.strip()
            
            import json
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(text[start:end])
                return QueryIntent(**data)
        except Exception as e:
            pass
        
        # Fallback: basic intent
        return QueryIntent(
            query_type="list",
            target_entity="bill",
        )
    
    def _resolve_time_reference(
        self,
        time_ref: Optional[str],
    ) -> tuple[Optional[date], Optional[date]]:
        """
        Convert natural language time reference to date range.
        
        This is DETERMINISTIC - no LLM involvement.
        """
        if not time_ref:
            return None, None
        
        time_ref = time_ref.lower().strip()
        today = date.today()
        
        # Current month
        if time_ref in ["this month", "current month"]:
            start = today.replace(day=1)
            if today.month == 12:
                end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
            return start, end
        
        # Last month
        if time_ref in ["last month", "previous month"]:
            first_of_this_month = today.replace(day=1)
            end = first_of_this_month - timedelta(days=1)
            start = end.replace(day=1)
            return start, end
        
        # This year
        if time_ref in ["this year", "current year"]:
            start = today.replace(month=1, day=1)
            end = today.replace(month=12, day=31)
            return start, end
        
        # Last year
        if time_ref in ["last year", "previous year"]:
            start = today.replace(year=today.year - 1, month=1, day=1)
            end = today.replace(year=today.year - 1, month=12, day=31)
            return start, end
        
        # Specific months
        months = {
            "january": 1, "february": 2, "march": 3, "april": 4,
            "may": 5, "june": 6, "july": 7, "august": 8,
            "september": 9, "october": 10, "november": 11, "december": 12,
        }
        
        for month_name, month_num in months.items():
            if month_name in time_ref:
                # Assume current year unless past
                year = today.year
                if month_num > today.month:
                    year -= 1  # Must be last year
                
                start = date(year, month_num, 1)
                if month_num == 12:
                    end = date(year + 1, 1, 1) - timedelta(days=1)
                else:
                    end = date(year, month_num + 1, 1) - timedelta(days=1)
                return start, end
        
        # Year mentioned (e.g., "2024")
        import re
        year_match = re.search(r"20\d{2}", time_ref)
        if year_match:
            year = int(year_match.group())
            start = date(year, 1, 1)
            end = date(year, 12, 31)
            return start, end
        
        return None, None
    
    def intent_to_query(
        self,
        intent: QueryIntent,
        original_question: str,
    ) -> StructuredQuery:
        """
        Convert parsed intent to executable structured query.
        
        This is DETERMINISTIC - maps intent fields to query parameters.
        """
        # Resolve time reference
        date_from, date_to = self._resolve_time_reference(intent.time_reference)
        
        # Map category
        category_filter = None
        if intent.category:
            try:
                category_filter = BillCategory(intent.category.lower())
            except ValueError:
                pass
        
        # Map payment status
        payment_filter = None
        if intent.payment_status:
            status_lower = intent.payment_status.lower()
            if "paid" in status_lower and "un" not in status_lower:
                payment_filter = PaymentStatus.PAID
            elif "unpaid" in status_lower or "not paid" in status_lower:
                payment_filter = PaymentStatus.UNPAID
            elif "overdue" in status_lower:
                payment_filter = PaymentStatus.OVERDUE
        
        # Map aggregation
        agg_type = None
        if intent.aggregation:
            agg_lower = intent.aggregation.lower()
            if agg_lower in ["sum", "total"]:
                agg_type = "sum"
            elif agg_lower in ["count", "number"]:
                agg_type = "count"
            elif agg_lower in ["average", "avg", "mean"]:
                agg_type = "average"
            elif agg_lower in ["min", "minimum", "lowest"]:
                agg_type = "min"
            elif agg_lower in ["max", "maximum", "highest"]:
                agg_type = "max"
        
        # Map group_by
        group = None
        if intent.group_by:
            if intent.group_by.lower() in ["category", "type"]:
                group = "category"
            elif intent.group_by.lower() in ["vendor", "company"]:
                group = "vendor"
            elif intent.group_by.lower() in ["month"]:
                group = "month"
            elif intent.group_by.lower() in ["year"]:
                group = "year"
        
        return StructuredQuery(
            original_question=original_question,
            query_type=intent.query_type,
            category_filter=category_filter,
            vendor_filter=intent.vendor,
            date_from=date_from,
            date_to=date_to,
            payment_status_filter=payment_filter,
            aggregation_type=agg_type,
            group_by=group,
        )
    
    async def generate_response(
        self,
        query: StructuredQuery,
        result: QueryResult,
    ) -> NaturalLanguageResponse:
        """
        Generate a natural language response from query results.
        
        CRITICAL: The LLM can ONLY use the data provided.
        It CANNOT invent or supplement with its own knowledge.
        """
        # Build context from actual data
        if not result.data_found:
            # No data - must say so explicitly
            return NaturalLanguageResponse(
                response=(
                    f"I don't have any records matching your question. "
                    f"({query.query_description})"
                ),
                confidence=1.0,
                data_used=False,
            )
        
        # Format the data for the LLM
        data_summary = []
        
        if result.aggregation_result:
            for key, value in result.aggregation_result.items():
                if isinstance(value, (int, float, Decimal)):
                    data_summary.append(f"{key}: ₹{value:,.2f}" if "amount" in key.lower() or "total" in key.lower() else f"{key}: {value}")
                else:
                    data_summary.append(f"{key}: {value}")
        
        if result.results:
            # Format individual results
            for item in result.results[:5]:  # Limit to 5 for response
                parts = []
                if "vendor_name" in item:
                    parts.append(item["vendor_name"])
                if "total_amount" in item:
                    parts.append(f"₹{item['total_amount']:,.2f}")
                if "bill_date" in item:
                    parts.append(str(item["bill_date"]))
                if "payment_status" in item:
                    parts.append(item["payment_status"])
                if parts:
                    data_summary.append(" | ".join(parts))
        
        data_str = "\n".join(data_summary) or "No details available"
        
        prompt = f"""You are answering a question about personal bills using ONLY the data provided.

Original question: "{query.original_question}"

Query performed: {query.query_description}

Results found: {result.result_count}

Data:
{data_str}

Generate a natural, helpful response for an Indian parent user.
- Use simple language
- Format amounts in Indian Rupees (₹)
- If asked yes/no, answer clearly first
- Keep it concise

IMPORTANT: Use ONLY the data above. Do NOT add any information not in the data.
If the data doesn't fully answer the question, say what you can and acknowledge the limitation."""

        try:
            response = await self._model.generate_content_async(prompt)
            return NaturalLanguageResponse(
                response=response.text.strip(),
                confidence=0.9,
                data_used=True,
            )
        except Exception as e:
            # Fallback to basic response
            if result.aggregation_result:
                return NaturalLanguageResponse(
                    response=f"Based on your records: {data_str}",
                    confidence=0.7,
                    data_used=True,
                )
            else:
                return NaturalLanguageResponse(
                    response=f"Found {result.result_count} matching records.",
                    confidence=0.6,
                    data_used=True,
                )
