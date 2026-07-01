"""
EcoTrace AI - Report Generation Agent

Generates the final deliverable: executive summary, emission hotspots,
supplier rankings, recommendations, improvement roadmap. Exports both
Markdown and PDF.
"""
from datetime import datetime
from pathlib import Path
from typing import Any

from fpdf import FPDF

from llm_client import chat
import config

EXEC_SUMMARY_PROMPT = """You are the Report Generation Agent for EcoTrace AI.
Write a concise executive summary (4-6 sentences) of a supply-chain carbon
audit, given the aggregate data below. Mention the total emissions, the
single biggest emission driver, and the headline opportunity for reduction.
Plain business language, no jargon, no markdown headers - just prose.
"""


def _generate_exec_summary(summary: dict, top_recommendation: dict | None) -> str:
    context = (
        f"Total emissions: {summary.get('total_co2e_kg')} kg CO2e across "
        f"{summary.get('supplier_count')} suppliers. "
        f"Emissions by transport mode: {summary.get('emissions_by_transport_mode')}. "
    )
    if top_recommendation:
        context += (
            f"Top recommendation: {top_recommendation.get('title')}, "
            f"estimated savings {top_recommendation.get('estimated_co2e_savings_kg')} kg CO2e "
            f"({top_recommendation.get('pct_reduction')}% reduction)."
        )
    result = chat(
        messages=[
            {"role": "system", "content": EXEC_SUMMARY_PROMPT},
            {"role": "user", "content": context},
        ],
        temperature=0.3,
    )
    return result["content"]


def build_markdown_report(
    summary: dict[str, Any],
    scored_suppliers: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    explanation: str,
) -> str:
    top_rec = recommendations[0] if recommendations else None
    exec_summary = _generate_exec_summary(summary, top_rec)
    date_str = datetime.now().strftime("%Y-%m-%d")

    lines = [
        "# EcoTrace AI — Supply Chain Carbon Audit Report",
        f"*Generated {date_str}*",
        "",
        "## Executive Summary",
        exec_summary,
        "",
        "## Emission Overview",
        f"- **Total estimated emissions:** {summary.get('total_co2e_kg', 0):,.2f} kg CO2e",
        f"- **Suppliers analyzed:** {summary.get('supplier_count', 0)}",
        "",
        "### Emissions by Transport Mode",
    ]
    for mode, val in summary.get("emissions_by_transport_mode", {}).items():
        lines.append(f"- {mode.title()}: {val:,.2f} kg CO2e")

    lines += ["", "## Emission Hotspots & Reasoning", explanation, ""]

    lines += ["## Supplier Sustainability Rankings", "", "| Rank | Supplier | Score | Country | Notes |", "|---|---|---|---|---|"]
    for i, s in enumerate(scored_suppliers, start=1):
        lines.append(
            f"| {i} | {s['supplier']} | {s['score']}/100 | {s.get('country', 'N/A')} | "
            f"{', '.join(s.get('certifications', [])) or 'No certifications on file'} |"
        )

    lines += ["", "## Recommendations & Improvement Roadmap", ""]
    for i, rec in enumerate(recommendations, start=1):
        lines.append(f"### {i}. {rec.get('title')} — *Priority: {rec.get('priority', 'n/a').title()}*")
        lines.append(f"- Estimated savings: {rec.get('estimated_co2e_savings_kg', 0):,.2f} kg CO2e "
                      f"({rec.get('pct_reduction', 0)}% reduction)")
        lines.append(f"- Trade-offs: {rec.get('tradeoffs', 'N/A')}")
        lines.append("")

    return "\n".join(lines)


def export_markdown(markdown_content: str, filename: str = "ecotrace_report.md") -> Path:
    path = config.REPORTS_DIR / filename
    path.write_text(markdown_content, encoding="utf-8")
    return path


def export_pdf(markdown_content: str, filename: str = "ecotrace_report.pdf") -> Path:
    """Simple PDF export: strips markdown syntax down to readable plain text
    with basic heading emphasis. Not a full markdown renderer by design -
    keeps the dependency footprint light (fpdf2 only)."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)

    for raw_line in markdown_content.split("\n"):
        line = raw_line.strip()
        if not line:
            pdf.ln(3)
            continue
        if line.startswith("# "):
            pdf.set_font("Helvetica", "B", 18)
            pdf.multi_cell(0, 10, line[2:])
            pdf.set_font("Helvetica", size=11)
        elif line.startswith("## "):
            pdf.set_font("Helvetica", "B", 14)
            pdf.multi_cell(0, 9, line[3:])
            pdf.set_font("Helvetica", size=11)
        elif line.startswith("### "):
            pdf.set_font("Helvetica", "B", 12)
            pdf.multi_cell(0, 8, line[4:])
            pdf.set_font("Helvetica", size=11)
        elif line.startswith("|"):
            pdf.set_font("Courier", size=9)
            pdf.multi_cell(0, 6, line)
            pdf.set_font("Helvetica", size=11)
        elif line.startswith("- "):
            pdf.multi_cell(0, 6, f"  \u2022 {line[2:]}")
        else:
            clean = line.replace("**", "").replace("*", "")
            pdf.multi_cell(0, 6, clean)

    path = config.REPORTS_DIR / filename
    pdf.output(str(path))
    return path
