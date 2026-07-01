"""
EcoTrace AI - Scenario Simulator Page

Lets the user explore a hypothetical supply-chain change - either via
structured form inputs (supplier, transport mode, region, shipment
frequency) or a natural-language description - and instantly see the
environmental, cost, and operational impact versus the current baseline.

Pipeline: Carbon Estimation Agent (baseline) -> Scenario Simulator Agent
(scenario_agent.py: parse/build ScenarioRequest -> simulation_engine.py:
apply modification, recalculate, compare, recommend -> LLM narration of
trade-offs). Same "sidebar form + button + before/after charts" shape as
the Goal Optimizer page, so the two what-if-style features feel
consistent across the app.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from utils.session import init_session_state
from utils.theme import CUSTOM_CSS, COLORS, TRANSPORT_COLORS
from agents import carbon_estimation_agent
import scenario_agent

st.set_page_config(page_title="EcoTrace AI — Scenario Simulator", page_icon="🔀", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_session_state()

st.markdown('<div class="ecotrace-eyebrow">SUPPLY CHAIN SCENARIO SIMULATOR AGENT</div>', unsafe_allow_html=True)
st.title("Scenario Simulator")
st.caption(
    "Explore a hypothetical change — switch transport modes, swap suppliers, source from a "
    "different region, consolidate shipments — and instantly see the emissions, cost, and "
    "shipping-time impact versus your current supply chain."
)

records = st.session_state.structured_records

if not records:
    st.info("No data yet. Go to **Upload Data** first.")
    st.stop()

with st.spinner("Running Carbon Estimation Agent..."):
    estimates = carbon_estimation_agent.estimate_all(records)

current_total_kg = sum(e["total_co2e_kg"] for e in estimates if "error" not in e)
supplier_names = sorted({e["supplier"] for e in estimates if "error" not in e})

SCENARIO_TYPE_LABELS = {
    "transport_switch": "Transport change (Air → Sea, Air → Rail, Sea → Rail, ...)",
    "supplier_replacement": "Replace supplier",
    "regional_sourcing": "Local / regional sourcing",
    "consolidation": "Shipment consolidation",
    "route_optimization": "Route optimization",
    "warehouse_relocation": "Warehouse / manufacturing relocation",
}

# --- Sidebar: Scenario inputs -------------------------------------------
with st.sidebar:
    st.header("Scenario Inputs")

    scenario_input_mode = st.radio(
        "How do you want to describe the scenario?",
        ["Form", "Natural language"],
        horizontal=True,
        label_visibility="collapsed",
    )

    nl_request = None
    scenario_type = None
    target_suppliers = []
    from_mode = to_mode = None
    replacement_supplier = None
    target_region = None
    prioritize_local = False
    shipment_frequency = None

    if scenario_input_mode == "Natural language":
        nl_request = st.text_area(
            "Describe the hypothetical change",
            placeholder='e.g. "What happens if we replace air freight with sea freight?" or '
            '"What if Supplier A is replaced by Supplier C?"',
            height=100,
        )
        st.caption("The agent will parse this into a scenario type, target supplier(s), and modification.")
    else:
        scenario_type = st.selectbox(
            "Scenario type",
            list(SCENARIO_TYPE_LABELS.keys()),
            format_func=lambda k: SCENARIO_TYPE_LABELS[k],
        )

        # Supplier selection - applies to every scenario type
        target_suppliers = st.multiselect(
            "Supplier selection",
            supplier_names,
            default=[],
            help="Leave empty to apply this scenario portfolio-wide.",
        )

        if scenario_type == "transport_switch":
            from_mode = st.selectbox(
                "From transport mode", ["(any current mode)", "air", "sea", "road", "rail", "local"]
            )
            from_mode = None if from_mode == "(any current mode)" else from_mode
            to_mode = st.selectbox(
                "Transport mode selection (switch to)", ["(auto - next lower-carbon mode)", "air", "sea", "road", "rail", "local"]
            )
            to_mode = None if to_mode == "(auto - next lower-carbon mode)" else to_mode

        elif scenario_type == "supplier_replacement":
            replacement_supplier = st.selectbox(
                "Replace with supplier", ["(hypothetical best-in-class)"] + supplier_names
            )
            replacement_supplier = (
                None if replacement_supplier == "(hypothetical best-in-class)" else replacement_supplier
            )

        elif scenario_type == "regional_sourcing":
            known_regions = sorted({e.get("country") for e in estimates if "error" not in e and e.get("country")})
            region_choice = st.selectbox("Region selection (source from)", ["(generic local/regional)"] + known_regions)
            target_region = None if region_choice == "(generic local/regional)" else region_choice
            prioritize_local = st.toggle("Prioritize local sourcing", value=(target_region is None))

        if scenario_type in ("consolidation", "route_optimization"):
            shipment_frequency = st.selectbox(
                "Shipment frequency", ["(unchanged)", "weekly", "bi-weekly", "monthly"]
            )
            shipment_frequency = None if shipment_frequency == "(unchanged)" else shipment_frequency

    run_clicked = st.button("Run Simulation", type="primary", use_container_width=True)

# --- Run scenario on button click ---------------------------------------
if run_clicked:
    if scenario_input_mode == "Natural language":
        if not nl_request or not nl_request.strip():
            st.sidebar.error("Please describe the scenario first.")
            st.stop()
        with st.spinner("Parsing your scenario..."):
            scenario = scenario_agent.parse_scenario(nl_request, known_suppliers=supplier_names)
    else:
        scenario = scenario_agent.build_scenario_from_form(
            scenario_type=scenario_type,
            target_suppliers=target_suppliers,
            from_transport_mode=from_mode,
            to_transport_mode=to_mode,
            replacement_supplier=replacement_supplier,
            target_region=target_region,
            prioritize_local_sourcing=prioritize_local,
            shipment_frequency=shipment_frequency,
        )

    with st.spinner("Applying scenario and recalculating emissions..."):
        scenario_run = scenario_agent.run_scenario(scenario, estimates)

    st.session_state["last_scenario_run"] = scenario_run
    st.session_state.memory.save_scenario(
        {
            "scenario_type": scenario_run["scenario"]["scenario_type"],
            "raw_request": scenario_run["scenario"].get("raw_request"),
            "current_emissions_kg": scenario_run["result"]["current_emissions_kg"],
            "scenario_emissions_kg": scenario_run["result"]["scenario_emissions_kg"],
            "pct_difference": scenario_run["result"]["pct_difference"],
            "recommendation": scenario_run["result"]["recommendation"],
        }
    )

# --- Display results -----------------------------------------------------
scenario_run = st.session_state.get("last_scenario_run")

if not scenario_run:
    st.markdown("#### Current state")
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Current emissions", f"{current_total_kg:,.0f} kg CO2e")
    st.markdown("</div>", unsafe_allow_html=True)
    st.info("Describe or configure a scenario in the sidebar and click **Run Simulation** to see results.")
    st.stop()

result = scenario_run["result"]
scenario_dict = scenario_run["scenario"]

# --- Top metrics: current vs scenario ------------------------------------
m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Current emissions", f"{result['current_emissions_kg']:,.0f} kg CO2e")
    st.markdown("</div>", unsafe_allow_html=True)
with m2:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric(
        "Scenario emissions",
        f"{result['scenario_emissions_kg']:,.0f} kg CO2e",
        delta=f"{result['pct_difference']:+.1f}%",
        delta_color="inverse",
    )
    st.markdown("</div>", unsafe_allow_html=True)
with m3:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Difference", f"{result['pct_difference']:+.1f}%")
    st.markdown("</div>", unsafe_allow_html=True)
with m4:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Operational impact", f"{result['leadtime_delta_days']:+.1f} days shipping time")
    st.markdown("</div>", unsafe_allow_html=True)
with m5:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Estimated cost impact", f"{result['cost_delta_pct']:+.1f}%")
    st.markdown("</div>", unsafe_allow_html=True)

rec = result["recommendation"]
rec_style = {
    "Highly recommended": st.success,
    "Recommended": st.success,
    "Conditional": st.warning,
    "Not recommended": st.error,
}.get(rec, st.info)
rec_style(f"**Recommendation: {rec}.** {result['recommendation_reason']}")

st.divider()

# --- Tradeoff narrative ----------------------------------------------------
st.subheader("Explaining the Trade-offs")
st.markdown(scenario_run["narrative"])
if result.get("notes"):
    for note in result["notes"]:
        st.caption(f"ℹ️ {note}")

st.divider()

# --- Visualizations --------------------------------------------------------
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("Before vs. After Emissions")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=["Current", "Scenario"],
        y=[result["current_emissions_kg"], result["scenario_emissions_kg"]],
        marker_color=[COLORS["rust"], COLORS["moss_bright"]],
        text=[f"{result['current_emissions_kg']:,.0f} kg", f"{result['scenario_emissions_kg']:,.0f} kg"],
        textposition="outside",
    ))
    fig.update_layout(
        plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
        font_color=COLORS["ink"], yaxis_title="kg CO2e",
        margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

with chart_col2:
    st.subheader("Emission Contribution: Current vs. Scenario")
    affected = result["affected_suppliers"]
    if affected:
        df_contrib = pd.DataFrame(
            [
                {"supplier": s["supplier"], "state": "Current", "co2e_kg": s["baseline_co2e_kg"]}
                for s in affected
            ] + [
                {"supplier": s["supplier"], "state": "Scenario", "co2e_kg": s["scenario_co2e_kg"]}
                for s in affected
            ]
        )
        fig2 = px.bar(
            df_contrib, x="co2e_kg", y="supplier", color="state", orientation="h", barmode="group",
            color_discrete_map={"Current": COLORS["rust"], "Scenario": COLORS["moss_bright"]},
        )
        fig2.update_layout(
            plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
            font_color=COLORS["ink"], xaxis_title="kg CO2e", yaxis_title="",
            margin=dict(l=10, r=10, t=10, b=10), legend_title_text="",
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No suppliers were affected by this scenario.")

st.subheader("Supplier Comparison")
affected = result["affected_suppliers"]
if affected:
    df_supplier_cmp = pd.DataFrame(
        [
            {
                "Supplier": s["supplier"],
                "Baseline mode": s["baseline_transport_mode"].title(),
                "Scenario mode": s["scenario_transport_mode"].title(),
                "Baseline CO2e (kg)": s["baseline_co2e_kg"],
                "Scenario CO2e (kg)": s["scenario_co2e_kg"],
                "Savings (kg)": s["co2e_savings_kg"],
            }
            for s in affected
        ]
    ).sort_values("Savings (kg)", ascending=False)

    fig3 = px.bar(
        df_supplier_cmp, x="Savings (kg)", y="Supplier", orientation="h",
        color="Baseline mode", color_discrete_map=TRANSPORT_COLORS,
    )
    fig3.update_layout(
        plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
        font_color=COLORS["ink"], xaxis_title="kg CO2e saved (negative = increase)", yaxis_title="",
        margin=dict(l=10, r=10, t=10, b=10), legend_title_text="Current transport mode",
    )
    st.plotly_chart(fig3, use_container_width=True)

    st.dataframe(df_supplier_cmp, use_container_width=True, hide_index=True)
else:
    st.info("No suppliers were affected by this scenario.")

st.divider()
st.caption(
    f"Scenario type: **{SCENARIO_TYPE_LABELS.get(scenario_dict['scenario_type'], scenario_dict['scenario_type'])}**"
    + (f" · Suppliers: {', '.join(scenario_dict['target_suppliers'])}" if scenario_dict.get("target_suppliers") else " · Applied portfolio-wide")
)
