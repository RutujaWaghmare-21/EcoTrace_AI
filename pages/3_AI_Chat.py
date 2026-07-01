"""
EcoTrace AI - AI Chat Page

Interactive conversation with the agent. Every message is routed through
the Planner Agent, which decides intent, calls the relevant sub-agents
and tools, and synthesizes a grounded answer. Shows the agent's plan/
reasoning trail in an expander for transparency.
"""
import streamlit as st

from utils.session import init_session_state
from utils.theme import CUSTOM_CSS
from agents import planner_agent

st.set_page_config(page_title="EcoTrace AI — Chat", page_icon="💬", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_session_state()

st.markdown('<div class="ecotrace-eyebrow">PLANNER AGENT</div>', unsafe_allow_html=True)
st.title("Ask EcoTrace AI")
st.caption(
    "Try: \"Audit my supply chain\" · \"Which suppliers contribute the highest emissions?\" · "
    "\"How can I reduce emissions by 20%?\" · \"What happens if we replace air freight with sea freight?\" · "
    "\"What if Supplier A is replaced by Supplier C?\" · \"What if we consolidate shipments?\""
)

if not st.session_state.structured_records:
    st.warning("No supplier data uploaded yet — answers will be limited. Visit **Upload Data** first for best results.")

for msg in st.session_state.chat_messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg["role"] == "assistant" and msg.get("trace"):
            with st.expander("Show agent reasoning trail"):
                st.json(msg["trace"])

user_input = st.chat_input("Ask a question about your supply chain...")

if user_input:
    st.session_state.chat_messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)

    with st.chat_message("assistant"):
        with st.spinner("Planning and analyzing..."):
            result = planner_agent.run(
                user_message=user_input,
                structured_records=st.session_state.structured_records,
                vector_store=st.session_state.vector_store,
                memory=st.session_state.memory,
            )
        st.markdown(result["answer"])
        with st.expander("Show agent reasoning trail"):
            st.json(result["plan"])
            st.caption(f"RAG chunks used: {result['data'].get('rag_chunks_used', 0)}")

    st.session_state.chat_messages.append(
        {"role": "assistant", "content": result["answer"], "trace": result["plan"]}
    )
    st.session_state.last_plan_trace = result["plan"]

if st.session_state.chat_messages:
    if st.button("Clear conversation"):
        st.session_state.chat_messages = []
        st.rerun()
