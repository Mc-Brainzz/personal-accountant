"""
Streamlit Frontend for Personal Accountant

This is the user interface that non-technical users (Indian parents)
will interact with daily.

DESIGN PRINCIPLES:
1. Simple, clear interface
2. Explicit confirmation at every step
3. Clear error messages in simple language
4. Visual feedback for all operations
5. No hidden actions

The UI enforces the human-in-the-loop principle:
- User sees what was extracted
- User confirms or edits
- Nothing is saved without explicit "Save" action
"""

import asyncio
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

import streamlit as st

from src.audit import create_correlation_id
from src.models.bill import BillCategory, ExtractedBillData, PaymentStatus
from src.orchestrator import create_app_components, BillUploadFlow, QueryFlow


# Page configuration
st.set_page_config(
    page_title="Personal Accountant",
    page_icon="💰",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for better UX
st.markdown("""
<style>
    .stButton>button {
        width: 100%;
        margin-top: 10px;
    }
    .success-box {
        padding: 20px;
        background-color: #d4edda;
        border-radius: 10px;
        border-left: 5px solid #28a745;
        margin: 10px 0;
    }
    .warning-box {
        padding: 20px;
        background-color: #fff3cd;
        border-radius: 10px;
        border-left: 5px solid #ffc107;
        margin: 10px 0;
    }
    .error-box {
        padding: 20px;
        background-color: #f8d7da;
        border-radius: 10px;
        border-left: 5px solid #dc3545;
        margin: 10px 0;
    }
    .info-box {
        padding: 20px;
        background-color: #cce5ff;
        border-radius: 10px;
        border-left: 5px solid #004085;
        margin: 10px 0;
    }
    .big-number {
        font-size: 2.5em;
        font-weight: bold;
        color: #2c3e50;
    }
</style>
""", unsafe_allow_html=True)


def run_async(coro):
    """Helper to run async functions in Streamlit."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@st.cache_resource
def get_components():
    """Get or create application components (cached)."""
    try:
        return create_app_components(use_storage=True)
    except Exception as e:
        st.error(f"Failed to initialize: {e}")
        return create_app_components(use_storage=False)


def main():
    """Main application entry point."""
    # Initialize components
    bill_flow, query_flow, _ = get_components()
    
    # Sidebar navigation
    st.sidebar.title("💰 Personal Accountant")
    st.sidebar.markdown("---")
    
    page = st.sidebar.radio(
        "Navigate to:",
        ["📤 Upload Bill", "❓ Ask Question", "📊 View Bills", "⚙️ Settings"],
        index=0,
    )
    
    st.sidebar.markdown("---")
    st.sidebar.markdown(
        """
        **How to use:**
        1. Upload your bill photo
        2. Review the extracted details
        3. Confirm to save
        
        **Ask questions like:**
        - "Did I pay electricity this month?"
        - "How much did I spend on groceries?"
        """
    )
    
    # Route to appropriate page
    if page == "📤 Upload Bill":
        render_upload_page(bill_flow)
    elif page == "❓ Ask Question":
        render_query_page(query_flow)
    elif page == "📊 View Bills":
        render_bills_page(bill_flow)
    elif page == "⚙️ Settings":
        render_settings_page()


def render_upload_page(bill_flow: BillUploadFlow):
    """Render the bill upload page."""
    st.title("📤 Upload Bill")
    st.markdown("Take a photo of your bill and upload it here.")
    
    # Initialize session state
    if "upload_state" not in st.session_state:
        st.session_state.upload_state = "idle"  # idle, processing, reviewing, saved
    if "extracted_data" not in st.session_state:
        st.session_state.extracted_data = None
    if "correlation_id" not in st.session_state:
        st.session_state.correlation_id = None
    if "enhanced_url" not in st.session_state:
        st.session_state.enhanced_url = None
    
    # Step 1: Upload
    uploaded_file = st.file_uploader(
        "Choose a bill photo",
        type=["jpg", "jpeg", "png", "webp"],
        help="Take a clear, well-lit photo of your bill",
    )
    
    if uploaded_file and st.session_state.upload_state == "idle":
        if st.button("🔍 Process Bill", type="primary"):
            st.session_state.correlation_id = create_correlation_id()
            st.session_state.upload_state = "processing"
            st.rerun()
    
    # Step 2: Processing
    if st.session_state.upload_state == "processing" and uploaded_file:
        with st.spinner("Processing your bill... Please wait."):
            try:
                # Read file
                image_bytes = uploaded_file.read()
                
                # Process image
                upload, enhanced, can_proceed, message = run_async(
                    bill_flow.process_image(
                        image_bytes=image_bytes,
                        filename=uploaded_file.name,
                        file_size=uploaded_file.size,
                        mime_type=uploaded_file.type,
                        correlation_id=st.session_state.correlation_id,
                    )
                )
                
                if not can_proceed:
                    st.session_state.upload_state = "idle"
                    st.markdown(f"""
                    <div class="error-box">
                        <h4>📷 Image Quality Issue</h4>
                        <p>{message}</p>
                        <p><strong>Please take a clearer photo and try again.</strong></p>
                    </div>
                    """, unsafe_allow_html=True)
                    st.stop()
                
                st.session_state.enhanced_url = enhanced.cloudinary_url
                
                # Extract bill data
                extracted, can_proceed, message = run_async(
                    bill_flow.extract_bill_data(
                        enhanced_image_url=enhanced.cloudinary_url,
                        upload_id=upload.upload_id,
                        correlation_id=st.session_state.correlation_id,
                    )
                )
                
                if not can_proceed:
                    st.session_state.upload_state = "idle"
                    st.markdown(f"""
                    <div class="error-box">
                        <h4>❌ Extraction Failed</h4>
                        <p>{message}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    st.stop()
                
                # Validate
                validation, val_message = run_async(
                    bill_flow.validate_extraction(
                        extracted=extracted,
                        correlation_id=st.session_state.correlation_id,
                    )
                )
                
                # Store for review
                st.session_state.extracted_data = extracted
                st.session_state.validation = validation
                st.session_state.val_message = val_message
                st.session_state.upload_state = "reviewing"
                st.rerun()
                
            except Exception as e:
                st.session_state.upload_state = "idle"
                st.error(f"Error processing bill: {str(e)}")
    
    # Step 3: Review and Confirm
    if st.session_state.upload_state == "reviewing":
        extracted = st.session_state.extracted_data
        validation = st.session_state.validation
        
        st.markdown("---")
        st.subheader("📋 Review Extracted Data")
        
        # Show validation message
        if validation.is_valid:
            st.markdown(f"""
            <div class="success-box">
                <h4>✅ Extraction Successful</h4>
                <p>Please review the details below and make any corrections.</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="warning-box">
                <h4>⚠️ Please Review</h4>
                <p>{st.session_state.val_message}</p>
            </div>
            """, unsafe_allow_html=True)
        
        # Show enhanced image
        if st.session_state.enhanced_url:
            with st.expander("📷 View Processed Image"):
                st.image(st.session_state.enhanced_url, width=400)
        
        # Editable form
        st.markdown("### Bill Details")
        st.markdown("*You can edit any field before saving*")
        
        col1, col2 = st.columns(2)
        
        with col1:
            vendor_name = st.text_input(
                "Vendor/Company Name *",
                value=extracted.vendor.name if extracted.vendor else "",
                help="Who is this bill from?",
            )
            
            # Get category suggestion
            if extracted:
                cat, conf, reason = run_async(
                    bill_flow.suggest_category(extracted)
                )
                default_cat_idx = list(BillCategory).index(cat)
            else:
                default_cat_idx = 0
            
            category = st.selectbox(
                "Category *",
                options=list(BillCategory),
                index=default_cat_idx,
                format_func=lambda x: x.value.replace("_", " ").title(),
                help="What type of bill is this?",
            )
            
            total_amount = st.number_input(
                "Total Amount (₹) *",
                value=float(extracted.total_amount) if extracted.total_amount else 0.0,
                min_value=0.0,
                step=0.01,
                format="%.2f",
                help="The total amount on the bill",
            )
        
        with col2:
            bill_date = st.date_input(
                "Bill Date *",
                value=extracted.bill_date or date.today(),
                help="The date on the bill",
            )
            
            due_date = st.date_input(
                "Due Date (optional)",
                value=extracted.due_date,
                help="When is the payment due?",
            )
            
            if extracted.bill_number:
                st.text_input(
                    "Bill Number",
                    value=extracted.bill_number,
                    disabled=True,
                )
        
        notes = st.text_area(
            "Notes (optional)",
            placeholder="Add any notes about this bill...",
            help="Any additional information you want to record",
        )
        
        # Confidence indicator
        if extracted.confidence_score:
            st.markdown(f"""
            **Extraction Confidence:** {extracted.confidence_score:.0%}
            {"🟢" if extracted.confidence_score >= 0.8 else "🟡" if extracted.confidence_score >= 0.6 else "🔴"}
            """)
        
        st.markdown("---")
        
        # Action buttons
        col1, col2, col3 = st.columns([2, 2, 1])
        
        with col1:
            if st.button("✅ Confirm and Save", type="primary"):
                if not vendor_name:
                    st.error("Please enter the vendor name")
                elif total_amount <= 0:
                    st.error("Please enter a valid amount")
                else:
                    try:
                        bill = run_async(
                            bill_flow.confirm_and_save(
                                extracted=extracted,
                                category=category,
                                vendor_name=vendor_name,
                                total_amount=Decimal(str(total_amount)),
                                bill_date=bill_date,
                                due_date=due_date,
                                notes=notes,
                                original_image_url=None,
                                enhanced_image_url=st.session_state.enhanced_url,
                                correlation_id=st.session_state.correlation_id,
                            )
                        )
                        st.session_state.upload_state = "saved"
                        st.session_state.saved_bill = bill
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to save: {str(e)}")
        
        with col2:
            if st.button("❌ Reject / Start Over"):
                run_async(
                    bill_flow.reject_extraction(
                        extracted=extracted,
                        reason="User rejected",
                        correlation_id=st.session_state.correlation_id,
                    )
                )
                st.session_state.upload_state = "idle"
                st.session_state.extracted_data = None
                st.rerun()
    
    # Step 4: Success
    if st.session_state.upload_state == "saved":
        bill = st.session_state.saved_bill
        
        st.markdown(f"""
        <div class="success-box">
            <h3>✅ Bill Saved Successfully!</h3>
            <p><strong>Vendor:</strong> {bill.vendor_name}</p>
            <p><strong>Amount:</strong> ₹{bill.total_amount:,.2f}</p>
            <p><strong>Category:</strong> {bill.category.value.replace('_', ' ').title()}</p>
            <p><strong>Date:</strong> {bill.bill_date.strftime('%d %B %Y')}</p>
        </div>
        """, unsafe_allow_html=True)
        
        if st.button("📤 Upload Another Bill"):
            st.session_state.upload_state = "idle"
            st.session_state.extracted_data = None
            st.session_state.saved_bill = None
            st.rerun()


def render_query_page(query_flow: QueryFlow):
    """Render the question/query page."""
    st.title("❓ Ask a Question")
    st.markdown("Ask anything about your bills and expenses.")
    
    # Example questions
    with st.expander("📝 Example Questions"):
        st.markdown("""
        - "Did I pay the electricity bill this month?"
        - "How much did I spend on groceries last month?"
        - "List all unpaid bills"
        - "What's my total spending this year?"
        - "Show my water bills from January"
        """)
    
    # Question input
    question = st.text_input(
        "Your question:",
        placeholder="e.g., Did I pay the electricity bill this month?",
        help="Ask about your bills in natural language",
    )
    
    if st.button("🔍 Get Answer", type="primary") and question:
        correlation_id = create_correlation_id()
        
        with st.spinner("Looking up your records..."):
            try:
                answer, result, query = run_async(
                    query_flow.answer_question(
                        question=question,
                        correlation_id=correlation_id,
                    )
                )
                
                # Show answer
                if result.data_found:
                    st.markdown(f"""
                    <div class="success-box">
                        <h4>📊 Answer</h4>
                        <p>{answer}</p>
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    st.markdown(f"""
                    <div class="info-box">
                        <h4>📋 Result</h4>
                        <p>{answer}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                # Show query details (collapsible)
                with st.expander("🔍 Query Details"):
                    if query:
                        st.markdown(f"**Query Type:** {query.query_type}")
                        st.markdown(f"**Description:** {result.query_description}")
                        st.markdown(f"**Records Found:** {result.result_count}")
                    
                    if result.results:
                        st.markdown("**Results:**")
                        for item in result.results[:5]:
                            st.json(item)
                            
            except Exception as e:
                st.error(f"Error: {str(e)}")


def render_bills_page(bill_flow: BillUploadFlow):
    """Render the bills list page."""
    st.title("📊 Your Bills")
    st.markdown("View and manage your saved bills.")
    
    # Filters
    col1, col2, col3 = st.columns(3)
    
    with col1:
        category_filter = st.selectbox(
            "Filter by Category",
            options=[None] + list(BillCategory),
            format_func=lambda x: "All Categories" if x is None else x.value.replace("_", " ").title(),
        )
    
    with col2:
        status_filter = st.selectbox(
            "Filter by Status",
            options=[None] + list(PaymentStatus),
            format_func=lambda x: "All Statuses" if x is None else x.value.replace("_", " ").title(),
        )
    
    with col3:
        date_range = st.date_input(
            "Date Range",
            value=[],
            help="Select date range",
        )
    
    st.markdown("---")
    
    # Note: In a full implementation, we would fetch from storage here
    st.info(
        "📋 Your bills will appear here once you upload them. "
        "Use the 'Upload Bill' page to add your first bill."
    )


def render_settings_page():
    """Render the settings page."""
    st.title("⚙️ Settings")
    
    st.markdown("### Connection Status")
    
    # Check services
    from src.config import validate_all_settings
    
    status = validate_all_settings()
    
    services = [
        ("Cloudinary (Image Enhancement)", "cloudinary"),
        ("Mindee (OCR)", "mindee"),
        ("Google Sheets (Storage)", "google_sheets"),
        ("Gemini (AI)", "gemini"),
    ]
    
    for name, key in services:
        if status.get(key, False):
            st.success(f"✅ {name} - Connected")
        else:
            error = status.get(f"{key}_error", "Not configured")
            st.error(f"❌ {name} - {error}")
    
    st.markdown("---")
    st.markdown("### Configuration")
    st.markdown(
        "To configure the application, create a `.env` file with your API keys. "
        "See `.env.example` for the required variables."
    )


if __name__ == "__main__":
    main()
