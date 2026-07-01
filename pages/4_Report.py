"""
EcoTrace AI - Report Page

Generates the final deliverable using the Report Generation Agent:
executive summary, emission hotspots, supplier rankings, recommendations,
improvement roadmap. Exports as Markdown and PDF.
"""
import streamlit as st

from utils.session import init_session_state
from utils.theme import CUSTOM_CSS
from agents import carbon_estimation_agent, supplier_risk_agent, optimization_agent, report_generation_agent

st.set_page_config(page_title="EcoTrace AI — Report", page_icon="📄", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_session_state()

st.markdown('<div class="ecotrace-eyebrow">REPORT GENERATION AGENT</div>', unsafe_allow_html=True)
st.title("Generate Audit Report")

records = st.session_state.structured_records

if not records:
    st.info("No data yet. Go to **Upload Data** first.")
    st.stop()

st.caption("Compiles results from every agent into a single executive-ready report.")

if st.button("Generate report", type="primary"):
    with st.spinner("Running full agent pipeline..."):
        estimates = carbon_estimation_agent.estimate_all(records)
        aggregate = carbon_estimation_agent.aggregate_summary(estimates)
        records_by_supplier = {r.get("supplier"): r for r in records if r.get("supplier")}
        scored = supplier_risk_agent.score_suppliers(estimates, records_by_supplier)
        explanation = carbon_estimation_agent.explain_estimates(estimates)
        recommendations = optimization_agent.generate_recommendations(estimates)

    with st.spinner("Writing executive summary and assembling report..."):
        markdown_report = report_generation_agent.build_markdown_report(
            summary=aggregate,
            scored_suppliers=scored,
            recommendations=recommendations,
            explanation=explanation,
        )
        md_path = report_generation_agent.export_markdown(markdown_report)
        pdf_path = report_generation_agent.export_pdf(markdown_report)

    st.session_state["last_report_markdown"] = markdown_report
    st.session_state["last_report_md_path"] = str(md_path)
    st.session_state["last_report_pdf_path"] = str(pdf_path)

    st.session_state.memory.save_recommendations(recommendations)
    st.success("Report generated.")

if "last_report_markdown" in st.session_state:
    st.divider()
    col1, col2 = st.columns(2)
    with col1:
        with open(st.session_state["last_report_md_path"], "rb") as f:
            st.download_button(
                "⬇ Download Markdown report", f, file_name="ecotrace_report.md", mime="text/markdown"
            )
    with col2:
        with open(st.session_state["last_report_pdf_path"], "rb") as f:
            st.download_button(
                "⬇ Download PDF report", f, file_name="ecotrace_report.pdf", mime="application/pdf"
            )

    st.divider()
    st.subheader("Preview")
    st.markdown(st.session_state["last_report_markdown"])
