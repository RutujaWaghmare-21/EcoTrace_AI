"""
EcoTrace AI - Analysis Page

Shows the supplier table, carbon estimates, and the four required
visualizations:
  - supplier ranking chart
  - emissions by transport mode
  - emissions by supplier
  - optimization impact chart
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.session import init_session_state
from utils.theme import CUSTOM_CSS, COLORS, TRANSPORT_COLORS
from agents import carbon_estimation_agent, supplier_risk_agent, optimization_agent

st.set_page_config(page_title="EcoTrace AI — Analysis", page_icon="📊", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_session_state()

st.markdown('<div class="ecotrace-eyebrow">CARBON ESTIMATION · SUPPLIER RISK · OPTIMIZATION AGENTS</div>', unsafe_allow_html=True)
st.title("Supply Chain Analysis")

records = st.session_state.structured_records

if not records:
    st.info("No data yet. Go to **Upload Data** first.")
    st.stop()

with st.spinner("Running Carbon Estimation Agent..."):
    estimates = carbon_estimation_agent.estimate_all(records)
    aggregate = carbon_estimation_agent.aggregate_summary(estimates)

records_by_supplier = {r.get("supplier"): r for r in records if r.get("supplier")}

with st.spinner("Running Supplier Risk Agent..."):
    scored = supplier_risk_agent.score_suppliers(estimates, records_by_supplier)

# --- Top metrics row ---------------------------------------------------
c1, c2, c3, c4 = st.columns(4)
with c1:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Total estimated emissions", f"{aggregate.get('total_co2e_kg', 0):,.0f} kg CO2e")
    st.markdown("</div>", unsafe_allow_html=True)
with c2:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Suppliers analyzed", aggregate.get("supplier_count", 0))
    st.markdown("</div>", unsafe_allow_html=True)
with c3:
    avg_score = round(sum(s["score"] for s in scored) / len(scored), 1) if scored else 0
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Avg. sustainability score", f"{avg_score}/100")
    st.markdown("</div>", unsafe_allow_html=True)
with c4:
    top_mode = max(aggregate.get("emissions_by_transport_mode", {"none": 0}).items(), key=lambda kv: kv[1])
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Top emission source", top_mode[0].title())
    st.markdown("</div>", unsafe_allow_html=True)

st.divider()

# --- Reasoning / explanation -------------------------------------------
with st.spinner("Generating reasoning explanation..."):
    explanation = carbon_estimation_agent.explain_estimates(estimates)
st.subheader("Why emissions look the way they do")
st.markdown(explanation)

st.divider()

# --- Charts --------------------------------------------------------------
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("Emissions by Supplier")
    valid_estimates = [e for e in estimates if "error" not in e]
    if valid_estimates:
        df_supplier = pd.DataFrame(
            [{"supplier": e["supplier"], "co2e_kg": e["total_co2e_kg"]} for e in valid_estimates]
        ).sort_values("co2e_kg", ascending=True)
        fig = px.bar(
            df_supplier, x="co2e_kg", y="supplier", orientation="h",
            color_discrete_sequence=[COLORS["moss"]],
        )
        fig.update_traces(marker_color=COLORS["rust"])
        fig.update_layout(
            plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
            font_color=COLORS["ink"], xaxis_title="kg CO2e", yaxis_title="",
            margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)

with chart_col2:
    st.subheader("Emissions by Transport Mode")
    by_mode = aggregate.get("emissions_by_transport_mode", {})
    if by_mode:
        df_mode = pd.DataFrame(list(by_mode.items()), columns=["mode", "co2e_kg"])
        fig2 = px.pie(
            df_mode, names="mode", values="co2e_kg", hole=0.45,
            color="mode", color_discrete_map=TRANSPORT_COLORS,
        )
        fig2.update_layout(
            plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
            font_color=COLORS["ink"], margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig2, use_container_width=True)

chart_col3, chart_col4 = st.columns(2)

with chart_col3:
    st.subheader("Supplier Sustainability Ranking")
    if scored:
        df_score = pd.DataFrame(scored).sort_values("score", ascending=True)
        fig3 = px.bar(
            df_score, x="score", y="supplier", orientation="h",
            range_x=[0, 100],
        )
        fig3.update_traces(marker_color=COLORS["moss_bright"])
        fig3.update_layout(
            plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
            font_color=COLORS["ink"], xaxis_title="Score (0-100)", yaxis_title="",
            margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig3, use_container_width=True)

with chart_col4:
    st.subheader("Optimization Impact")
    with st.spinner("Running Optimization Agent..."):
        recommendations = optimization_agent.generate_recommendations(estimates)
    valid_recs = [r for r in recommendations if r.get("estimated_co2e_savings_kg", 0) > 0]
    if valid_recs:
        df_recs = pd.DataFrame(valid_recs).sort_values("estimated_co2e_savings_kg", ascending=True)
        fig4 = px.bar(
            df_recs, x="estimated_co2e_savings_kg", y="title", orientation="h",
        )
        fig4.update_traces(marker_color=COLORS["amber"])
        fig4.update_layout(
            plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
            font_color=COLORS["ink"], xaxis_title="Potential kg CO2e savings", yaxis_title="",
            margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig4, use_container_width=True)
    else:
        st.info("No transport-switch optimization opportunities found — shipments already use lower-carbon modes.")

st.divider()

# --- Detailed tables -----------------------------------------------------
st.subheader("Supplier Detail Table")
detail_rows = []
for e in estimates:
    if "error" in e:
        continue
    score_entry = next((s for s in scored if s["supplier"] == e["supplier"]), {})
    detail_rows.append(
        {
            "Supplier": e["supplier"],
            "Country": e.get("country", "N/A"),
            "Material": e.get("material", "N/A"),
            "Transport": e["transport"]["transport_mode"].title(),
            "Distance (km)": e["transport"]["distance_km"],
            "Total CO2e (kg)": e["total_co2e_kg"],
            "Sustainability Score": score_entry.get("score", "N/A"),
        }
    )
st.dataframe(pd.DataFrame(detail_rows), use_container_width=True)

with st.expander("Recommendations (full list with trade-offs)"):
    for rec in recommendations:
        st.markdown(f"**{rec.get('title')}** — *{rec.get('priority', 'n/a').title()} priority*")
        st.markdown(
            f"Estimated savings: {rec.get('estimated_co2e_savings_kg', 0):,.1f} kg CO2e "
            f"({rec.get('pct_reduction', 0)}% reduction)"
        )
        st.caption(f"Trade-offs: {rec.get('tradeoffs', 'N/A')}")
        st.divider()

# Persist this run as an "analysis" in memory for continuity
st.session_state.memory.save_analysis(
    {"aggregate": aggregate, "scored_count": len(scored), "recommendation_count": len(recommendations)}
)
