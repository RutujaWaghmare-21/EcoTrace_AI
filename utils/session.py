"""
EcoTrace AI - Session State Helpers

Streamlit reruns the whole script on every interaction, so anything that
needs to persist across reruns within a session (and ideally across
restarts, via the on-disk MemoryStore/VectorStore) lives in st.session_state.
This module centralizes initialization so every page starts from the same
shape of state.
"""
import streamlit as st

from tools.vector_store import VectorStore
from tools.memory_store import MemoryStore


def init_session_state():
    if "vector_store" not in st.session_state:
        st.session_state.vector_store = VectorStore()
    if "memory" not in st.session_state:
        st.session_state.memory = MemoryStore()
    if "structured_records" not in st.session_state:
        # Hydrate from persisted memory on first load so a restarted app
        # still remembers previously uploaded suppliers.
        st.session_state.structured_records = st.session_state.memory.get_all_suppliers()
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    if "last_plan_trace" not in st.session_state:
        st.session_state.last_plan_trace = None
    if "uploaded_filenames" not in st.session_state:
        st.session_state.uploaded_filenames = set()


def add_records(new_records: list[dict]):
    """Add freshly extracted records to session state + persist to memory."""
    for record in new_records:
        st.session_state.structured_records.append(record)
        st.session_state.memory.upsert_supplier(record)
