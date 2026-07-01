"""
EcoTrace AI - Sustainability Goal Optimizer Page

Sidebar lets the user specify an optimization goal (target reduction %,
max cost increase %, preferred transport methods, local sourcing
preference, minimum supplier score) either via form controls or a
natural-language request. Clicking "Generate Optimization Plan" runs the
full pipeline: Carbon Estimation -> Supplier Risk -> Intervention
Generator -> Optimization Engine -> Sustainability Goal Optimization
Agent (narration). Displays current vs. optimized emissions, reduction %,
recommended actions, and before/after charts.
"""
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st

from utils.session import init_session_state
from utils.theme import CUSTOM_CSS, COLORS
from agents import carbon_estimation_agent, supplier_risk_agent
import optimization_goal_agent

st.set_page_config(page_title="EcoTrace AI — Goal Optimizer", page_icon="🎯", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_session_state()

st.markdown('<div class="ecotrace-eyebrow">SUSTAINABILITY GOAL OPTIMIZATION AGENT</div>', unsafe_allow_html=True)
st.title("Goal Optimizer")
st.caption(
    "Set a target — emissions reduction, cost ceiling, transport preferences — and the "
    "Optimization Engine will rank candidate interventions and build a plan that fits."
)

records = st.session_state.structured_records

if not records:
    st.info("No data yet. Go to **Upload Data** first.")
    st.stop()

# --- Sidebar: Optimization Goal form ------------------------------------
with st.sidebar:
    st.header("Optimization Goal")

    goal_input_mode = st.radio(
        "How do you want to set your goal?",
        ["Form", "Natural language"],
        horizontal=True,
        label_visibility="collapsed",
    )

    nl_request = None
    if goal_input_mode == "Natural language":
        nl_request = st.text_area(
            "Describe your goal",
            placeholder='e.g. "Reduce my carbon footprint by 30% while keeping additional costs below 5%."',
            height=100,
        )
        st.caption("The agent will parse this into a target %, cost ceiling, and transport preferences.")
    else:
        target_reduction_pct = st.slider("Target emission reduction (%)", 0, 80, 20)
        max_cost_increase_pct = st.slider("Maximum cost increase (%)", 0, 50, 10)
        preferred_transport_modes = st.multiselect(
            "Preferred transport methods",
            ["sea", "rail", "road", "local"],
            default=[],
            help="Interventions that switch TO these modes will be prioritized.",
        )
        reduce_modes = st.multiselect(
            "Transport methods to reduce usage of",
            ["air", "road", "sea", "rail"],
            default=[],
            help="e.g. select 'air' for 'minimize air freight'.",
        )
        prioritize_local_sourcing = st.toggle("Prioritize local suppliers", value=False)
        min_supplier_score = st.slider(
            "Maintain supplier sustainability score above",
            0, 100, 0,
            help="Set to 0 to disable this constraint.",
        )
        minimize_operational_change = st.toggle("Minimize operational changes", value=False)

    generate_clicked = st.button("Generate Optimization Plan", type="primary", use_container_width=True)

# --- Compute base data (always needed, cheap) ---------------------------
with st.spinner("Running Carbon Estimation & Supplier Risk agents..."):
    estimates = carbon_estimation_agent.estimate_all(records)
    records_by_supplier = {r.get("supplier"): r for r in records if r.get("supplier")}
    scored = supplier_risk_agent.score_suppliers(estimates, records_by_supplier)

current_total_kg = sum(e["total_co2e_kg"] for e in estimates if "error" not in e)

# --- Generate plan on button click --------------------------------------
if generate_clicked:
    if goal_input_mode == "Natural language":
        if not nl_request or not nl_request.strip():
            st.sidebar.error("Please describe your goal first.")
            st.stop()
        with st.spinner("Parsing your goal..."):
            goal = optimization_goal_agent.parse_goal(nl_request)
    else:
        goal = optimization_goal_agent.build_goal_from_form(
            target_reduction_pct=target_reduction_pct,
            max_cost_increase_pct=max_cost_increase_pct,
            preferred_transport_modes=preferred_transport_modes,
            reduce_modes=reduce_modes,
            prioritize_local_sourcing=prioritize_local_sourcing,
            min_supplier_score=min_supplier_score if min_supplier_score > 0 else None,
            minimize_operational_change=minimize_operational_change,
        )

    with st.spinner("Generating candidate interventions and optimizing..."):
        plan = optimization_goal_agent.generate_optimization_plan(
            goal=goal, records=records, estimates=estimates, scored_suppliers=scored
        )

    st.session_state["last_optimization_plan"] = plan
    st.session_state.memory.save_recommendations(
        [{"title": i["title"], "type": i["type"], "co2e_savings_kg": i["co2e_savings_kg"]}
         for i in plan["result"]["selected_interventions"]]
    )

# --- Display results -----------------------------------------------------
plan = st.session_state.get("last_optimization_plan")

if not plan:
    st.markdown("#### Current state")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
        st.metric("Current emissions", f"{current_total_kg:,.0f} kg CO2e")
        st.markdown("</div>", unsafe_allow_html=True)
    with c2:
        avg_score = round(sum(s["score"] for s in scored) / len(scored), 1) if scored else 0
        st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
        st.metric("Avg. sustainability score", f"{avg_score}/100")
        st.markdown("</div>", unsafe_allow_html=True)
    st.info("Set your goal in the sidebar and click **Generate Optimization Plan** to see recommendations.")
    st.stop()

result = plan["result"]
goal_dict = plan["goal"]

# --- Top metrics: current vs optimized ----------------------------------
m1, m2, m3, m4 = st.columns(4)
with m1:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Current emissions", f"{result['current_emissions_kg']:,.0f} kg CO2e")
    st.markdown("</div>", unsafe_allow_html=True)
with m2:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric(
        "Optimized emissions",
        f"{result['optimized_emissions_kg']:,.0f} kg CO2e",
        delta=f"-{result['total_reduction_pct']}%",
        delta_color="inverse",
    )
    st.markdown("</div>", unsafe_allow_html=True)
with m3:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    st.metric("Reduction achieved", f"{result['total_reduction_pct']}%",
              help=f"Target was {goal_dict['target_reduction_pct']}%")
    st.markdown("</div>", unsafe_allow_html=True)
with m4:
    st.markdown('<div class="ecotrace-metric-card">', unsafe_allow_html=True)
    cost_label = "savings" if result["total_cost_increase_pct"] < 0 else "increase"
    st.metric(f"Net cost {cost_label}", f"{abs(result['total_cost_increase_pct'])}%")
    st.markdown("</div>", unsafe_allow_html=True)

goal_met = result["goal_met"]
if goal_met:
    st.success(f"✅ Target of {goal_dict['target_reduction_pct']}% reduction met within constraints.")
else:
    st.warning(
        f"⚠️ Target of {goal_dict['target_reduction_pct']}% reduction was **not** fully reached "
        f"within the given constraints — see notes below."
    )

st.divider()

# --- Strategy narrative ----------------------------------------------------
st.subheader("Optimized Strategy")
st.markdown(plan["narrative"])
if result.get("notes"):
    for note in result["notes"]:
        st.caption(f"ℹ️ {note}")

st.divider()

# --- Before / After charts -------------------------------------------------
chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.subheader("Before vs. After Optimization")
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=["Current", "Optimized"],
        y=[result["current_emissions_kg"], result["optimized_emissions_kg"]],
        marker_color=[COLORS["rust"], COLORS["moss_bright"]],
        text=[f"{result['current_emissions_kg']:,.0f} kg", f"{result['optimized_emissions_kg']:,.0f} kg"],
        textposition="outside",
    ))
    fig.update_layout(
        plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
        font_color=COLORS["ink"], yaxis_title="kg CO2e",
        margin=dict(l=10, r=10, t=10, b=10), showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)

with chart_col2:
    st.subheader("Emissions Reduction by Intervention")
    selected = result["selected_interventions"]
    if selected:
        df_sel = pd.DataFrame(
            [{"title": i["title"], "savings": i["co2e_savings_kg"], "type": i["type"]} for i in selected]
        ).sort_values("savings", ascending=True)
        fig2 = px.bar(df_sel, x="savings", y="title", orientation="h", color="type")
        fig2.update_layout(
            plot_bgcolor=COLORS["bg_panel"], paper_bgcolor=COLORS["bg_panel"],
            font_color=COLORS["ink"], xaxis_title="kg CO2e saved", yaxis_title="",
            margin=dict(l=10, r=10, t=10, b=10), legend_title_text="Intervention type",
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No interventions were selected within the given constraints.")

st.divider()

# --- Recommended actions table ------------------------------------------
st.subheader("Recommended Actions")
if result["selected_interventions"]:
    for idx, i in enumerate(result["selected_interventions"], start=1):
        cost_word = "saves" if i["cost_delta_pct"] < 0 else "costs"
        st.markdown(
            f"**{idx}. {i['title']}**  \n"
            f"Emission reduction: **{i['co2e_savings_kg']:,.0f} kg CO2e** "
            f"({i['pct_reduction_of_supplier']}% of this supplier's emissions) · "
            f"Cost impact: **{cost_word} {abs(i['cost_delta_pct'])}%**"
        )
        st.caption(f"Trade-offs: {i['tradeoffs']}")
        st.divider()
else:
    st.info("No interventions selected — try relaxing the cost ceiling or lowering the target reduction.")

with st.expander(f"Other candidates considered but not selected ({len(result['rejected_interventions'])})"):
    if result["rejected_interventions"]:
        df_rej = pd.DataFrame(
            [
                {
                    "Intervention": i["title"],
                    "Type": i["type"],
                    "Potential savings (kg CO2e)": i["co2e_savings_kg"],
                    "Cost impact (%)": i["cost_delta_pct"],
                }
                for i in result["rejected_interventions"]
            ]
        )
        st.dataframe(df_rej, use_container_width=True, hide_index=True)
    else:
        st.caption("All generated candidates were selected.")
