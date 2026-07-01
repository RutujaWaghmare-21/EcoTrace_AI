"""
EcoTrace AI - Upload Page

Accepts supplier CSVs, invoices, shipping data, and sustainability
reports (PDF/CSV/XLSX/TXT). Runs the Document Extraction Agent on each
file, shows what was extracted, and indexes raw text into the vector
store for RAG.
"""
import streamlit as st
import pandas as pd

import config
from utils.session import init_session_state, add_records
from utils.theme import CUSTOM_CSS
from tools.document_parser import parse_file
from agents.document_extraction_agent import extract_from_parsed_file

st.set_page_config(page_title="EcoTrace AI — Upload", page_icon="📤", layout="wide")
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)
init_session_state()

st.markdown('<div class="ecotrace-eyebrow">DOCUMENT EXTRACTION AGENT</div>', unsafe_allow_html=True)
st.title("Upload Supply Chain Data")
st.caption("Supported formats: PDF, CSV, XLSX, TXT — supplier sheets, invoices, shipping logs, sustainability reports.")

uploaded_files = st.file_uploader(
    "Drop files here",
    type=["pdf", "csv", "xlsx", "xls", "txt"],
    accept_multiple_files=True,
)

if uploaded_files:
    new_filenames = [f.name for f in uploaded_files if f.name not in st.session_state.uploaded_filenames]

    if new_filenames:
        progress = st.progress(0.0, text="Starting extraction...")
        total_new_records = []

        files_to_process = [f for f in uploaded_files if f.name in new_filenames]
        for i, f in enumerate(files_to_process):
            progress.progress(i / len(files_to_process), text=f"Parsing {f.name}...")

            save_path = config.UPLOADS_DIR / f.name
            save_path.write_bytes(f.getvalue())

            try:
                parsed = parse_file(save_path)
            except Exception as e:  # noqa: BLE001
                st.error(f"Failed to parse {f.name}: {e}")
                continue

            progress.progress((i + 0.5) / len(files_to_process), text=f"Extracting structured data from {f.name}...")
            records, rag_indexed = extract_from_parsed_file(parsed, vector_store=st.session_state.vector_store)

            if records:
                add_records(records)
                total_new_records.extend(records)
            else:
                st.warning(f"No structured supplier/shipment data found in {f.name}.")

            if not rag_indexed:
                st.warning(
                    f"Could not index {f.name} for AI Chat / RAG search (the Gemini "
                    f"embeddings API call failed — check your GEMINI_API_KEY in .env, "
                    f"or your quota at aistudio.google.com/apikey). "
                    f"Structured data above was still extracted successfully."
                )

            st.session_state.uploaded_filenames.add(f.name)

        progress.progress(1.0, text="Done.")
        progress.empty()

        if total_new_records:
            st.success(f"Extracted {len(total_new_records)} supplier/shipment record(s) from {len(files_to_process)} file(s).")
            st.dataframe(pd.DataFrame(total_new_records), use_container_width=True)
    else:
        st.info("These files have already been processed this session.")

st.divider()

st.subheader("All records extracted so far")
if st.session_state.structured_records:
    df = pd.DataFrame(st.session_state.structured_records)
    st.dataframe(df, use_container_width=True)
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Total records", len(df))
    with col2:
        stats = st.session_state.vector_store.stats()
        st.metric("Documents indexed for RAG", len(stats["sources"]))

    with st.expander("Reset all uploaded data"):
        st.warning("This clears all extracted records, the vector index, and memory. This cannot be undone.")
        if st.button("Clear everything", type="secondary"):
            st.session_state.vector_store.clear()
            st.session_state.memory.clear_all()
            st.session_state.structured_records = []
            st.session_state.uploaded_filenames = set()
            st.session_state.chat_messages = []
            st.rerun()
else:
    st.info("No records yet. Upload files above to get started.")
