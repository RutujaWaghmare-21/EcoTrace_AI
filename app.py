"""
EcoTrace AI - Smart Supply Chain & Carbon Footprint Auditor
Main Streamlit entry point.

Run with:  streamlit run app.py
"""
import streamlit as st

from utils.session import init_session_state
from utils.theme import CUSTOM_CSS

st.set_page_config(
    page_title="EcoTrace AI",
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_session_state()

st.markdown(
    """
    <div class="ecotrace-eyebrow">SUPPLY CHAIN INTELLIGENCE</div>
    <h1 style="margin-top:-6px;">EcoTrace AI</h1>
    <p style="font-size:1.05rem; max-width:720px; color:#9FB3A8;">
    A multi-agent sustainability consultant that reads your supplier data,
    estimates carbon footprint, scores supplier risk, and recommends
    concrete emission reductions — with full reasoning at every step.
    </p>
    """,
    unsafe_allow_html=True,
)

st.divider()

col1, col2 = st.columns([1.3, 1])

with col1:
    st.subheader("How it works")
    st.markdown(
        """
        1. **Upload** supplier CSVs, invoices, shipping data, or sustainability PDFs on the **Upload Data** page.
        2. The **Document Extraction Agent** parses every file into structured supplier/shipment records, and indexes the raw text for retrieval (RAG).
        3. Head to **Analysis** to see carbon estimates, supplier sustainability scores, and charts — all computed by dedicated agents (Carbon Estimation, Supplier Risk, Optimization).
        4. Ask questions on the **AI Chat** page — e.g. *"Which suppliers contribute the highest emissions?"* or *"What happens if we switch from air to sea freight?"* The **Planner Agent** routes your question to the right tools and agents automatically.
        5. Explore hypothetical changes on the **Scenario Simulator** page — swap suppliers, switch transport modes, source from a new region, or consolidate shipments — and instantly see the emissions, cost, and shipping-time trade-offs versus your current baseline.
        6. Generate a full **Report** — markdown or PDF — with executive summary, hotspots, rankings, and a roadmap.
        """
    )

with col2:
    st.subheader("Current session")
    n_records = len(st.session_state.structured_records)
    n_chunks = st.session_state.vector_store.stats()["total_chunks"]
    st.metric("Supplier/shipment records", n_records)
    st.metric("Document chunks indexed (RAG)", n_chunks)
    if n_records == 0:
        st.info("No data yet — go to **Upload Data** in the sidebar to get started.")
    else:
        st.success("Data loaded. Visit **Analysis** or **AI Chat** to explore it.")

st.divider()
st.caption(
    "Architecture: Planner Agent → Document Extraction Agent → Carbon Estimation Agent → "
    "Supplier Risk Agent → Optimization Agent → Report Generation Agent, with shared RAG "
    "(FAISS) and persistent memory. The Scenario Simulator Agent and Sustainability Goal "
    "Optimization Agent extend this pipeline with deterministic what-if and goal-driven planning."
)
