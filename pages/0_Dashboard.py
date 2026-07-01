"""
EcoTrace AI - Dashboard Page

The at-a-glance landing view once data exists: total estimated emissions,
top emission sources, overall sustainability score. Deeper drill-down
lives on the Analysis page; this page is the executive snapshot.
"""
import pandas as pd
import plotly.express as px
import streamlit as st

from utils.session import init_session_state
from utils.theme import CUSTOM_CSS, COLORS
from agents import carbon_estimation_agent, supplier_risk_agent

st.set_page_config(page_title="EcoTrace AI — Dashboard", page_icon="🌍", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_session_state()

st.markdown('<div class="ecotrace-eyebrow">EXECUTIVE OVERVIEW</div>', unsafe_allow_html=True)
st.title("Dashboard")

records = st.session_state.structured_records

if not records:
    st.info("No data yet. Go to **Upload Data** to get started — your dashboard will populate automatically.")
    st.stop()

estimates = carbon_estimation_agent.estimate_all(records)
aggregate = carbon_estimation_agent.aggregate_summary(estimates)
records_by_supplier = {r.get("supplier"): r for r in records if r.get("supplier")}
scored = supplier_risk_agent.score_suppliers(estimates, records_by_supplier)

c1, c2, c3 = st.columns(3)
with c1:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.markdown('<div class="ecotrace-eyebrow">TOTAL ESTIMATED EMISSIONS</div>', unsafe_allow_html=True)
    st.markdown(f"<h2>{aggregate.get('total_co2e_kg', 0):,.0f} <span style='font-size:1rem;color:#9FB3A8;'>kg CO2e</span></h2>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

with c2:
    by_mode = aggregate.get("emissions_by_transport_mode", {})
    top_source = max(by_mode.items(), key=lambda kv: kv[1])[0].title() if by_mode else "N/A"
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.markdown('<div class="ecotrace-eyebrow">TOP EMISSION SOURCE</div>', unsafe_allow_html=True)
    st.markdown(f"<h2>{top_source} freight</h2>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

with c3:
    avg_score = round(sum(s["score"] for s in scored) / len(scored), 1) if scored else 0
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.markdown('<div class="ecotrace-eyebrow">OVERALL SUSTAINABILITY SCORE</div>', unsafe_allow_html=True)
    st.markdown(f"<h2>{avg_score}<span style='font-size:1rem;color:#9FB3A8;'>/100</span></h2>", unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)

st.divider()

col1, col2 = st.columns([1.4, 1])

with col1:
    st.subheader("Top Emission Sources")
    top_emitters = aggregate.get("top_emitters", [])
    if top_emitters:
        df = pd.DataFrame(
            [{"Supplier": e["supplier"], "CO2e (kg)": e["total_co2e_kg"],
              "Transport": e["transport"]["transport_mode"].title()} for e in top_emitters]
        )
        st.dataframe(df, use_container_width=True, hide_index=True)

with col2:
    st.subheader("Sustainability Score Distribution")
    if scored:
        df_score = pd.DataFrame(scored)
        fig = px.histogram(df_score, x="score", nbins=10, range_x=[0, 100])
        fig.update_traces(marker_color=COLORS["moss"])
        fig.update_layout(
            plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
            font_color=COLORS["ink"], xaxis_title="Score", yaxis_title="Suppliers",
            margin=dict(l=10, r=10, t=10, b=10), bargap=0.1,
        )
        st.plotly_chart(fig, use_container_width=True)

st.divider()
st.caption("For supplier-level detail, transport breakdowns, and optimization opportunities, see the **Analysis** page.")
