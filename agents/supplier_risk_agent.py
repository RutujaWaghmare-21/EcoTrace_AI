"""
EcoTrace AI - Supplier Risk Agent

Scores each supplier 0-100 based on:
  - emissions (lower = better)
  - transport distance (shorter = better)
  - sustainability certifications (bonus points)
  - renewable energy usage (bonus points)
  - environmental disclosures (presence of certification/renewable data
    at all counts as "disclosed", absence is penalized slightly since it
    signals lower transparency)

This is a deterministic scoring function (no LLM needed for the score
itself, ensuring reproducibility), but includes a short rationale string
per supplier for transparency/reasoning.
"""
from typing import Any

import config


def _score_emissions(total_co2e_kg: float, all_totals: list[float]) -> float:
    """Score 0-40: lower emissions relative to the supplier set scores higher."""
    if not all_totals or max(all_totals) == 0:
        return 40.0
    max_co2e = max(all_totals)
    # Inverse relative scaling: 0 emissions -> 40 pts, max emissions -> 0 pts
    return round(40 * (1 - (total_co2e_kg / max_co2e)), 1)


def _score_distance(distance_km: float, all_distances: list[float]) -> float:
    """Score 0-20: shorter distance scores higher."""
    if not all_distances or max(all_distances) == 0:
        return 20.0
    max_d = max(all_distances)
    return round(20 * (1 - (distance_km / max_d)), 1)


def _score_certifications(certifications: Any) -> tuple[float, list[str]]:
    """Score 0-30 (capped) from certification bonuses. Defensively handles
    any input shape - a clean list[str] is expected, but upstream extraction
    (CSV/XLSX NaN cells, LLM PDF extraction) can occasionally hand us a bare
    float, string, or None instead."""
    if not certifications or not isinstance(certifications, (list, tuple)):
        return 0.0, []
    matched = []
    score = 0.0
    for cert in certifications:
        if not isinstance(cert, str):
            continue
        key = cert.strip().lower()
        if not key:
            continue
        bonus = config.CERTIFICATION_BONUS.get(key)
        if bonus:
            score += bonus
            matched.append(cert)
    return round(min(score, 30.0), 1), matched


def _score_renewable(renewable_pct: Any) -> float:
    """Score 0-10 based on renewable energy %. Defensively handles None,
    NaN, and non-numeric inputs."""
    if renewable_pct is None:
        return 0.0
    try:
        pct = float(renewable_pct)
    except (TypeError, ValueError):
        return 0.0
    if pct != pct:  # NaN check
        return 0.0
    return round(min(max(pct, 0), 100) / 100 * 10, 1)


def score_suppliers(estimates: list[dict[str, Any]], records_by_supplier: dict[str, dict]) -> list[dict[str, Any]]:
    """
    `estimates` = output of carbon_estimation_agent.estimate_all
    `records_by_supplier` = original structured records keyed by supplier name,
        used to pull certifications / renewable_energy_pct (not part of the
        carbon calculation itself).
    """
    valid = [e for e in estimates if "error" not in e]
    all_totals = [e["total_co2e_kg"] for e in valid]
    all_distances = [e["transport"]["distance_km"] for e in valid]

    scored = []
    for e in valid:
        supplier = e["supplier"]
        record = records_by_supplier.get(supplier, {})
        certifications = record.get("certifications") or []
        renewable_pct = record.get("renewable_energy_pct")

        emissions_score = _score_emissions(e["total_co2e_kg"], all_totals)
        distance_score = _score_distance(e["transport"]["distance_km"], all_distances)
        cert_score, matched_certs = _score_certifications(certifications)
        renewable_score = _score_renewable(renewable_pct)

        total_score = round(min(emissions_score + distance_score + cert_score + renewable_score, 100), 1)

        disclosure_note = (
            "fully disclosed certifications & renewable energy data"
            if certifications and renewable_pct is not None
            else "partial sustainability disclosure"
            if certifications or renewable_pct is not None
            else "no certification or renewable energy data disclosed"
        )

        rationale = (
            f"{supplier} scores {total_score}/100: "
            f"emissions contribute {emissions_score}/40, "
            f"transport distance {distance_score}/20, "
            f"certifications ({', '.join(matched_certs) if matched_certs else 'none'}) "
            f"contribute {cert_score}/30, renewable energy use contributes "
            f"{renewable_score}/10. Disclosure level: {disclosure_note}."
        )

        scored.append(
            {
                "supplier": supplier,
                "country": e.get("country"),
                "score": total_score,
                "breakdown": {
                    "emissions_score": emissions_score,
                    "distance_score": distance_score,
                    "certification_score": cert_score,
                    "renewable_score": renewable_score,
                },
                "certifications": matched_certs,
                "renewable_energy_pct": renewable_pct,
                "rationale": rationale,
            }
        )

    return sorted(scored, key=lambda s: s["score"], reverse=True)
