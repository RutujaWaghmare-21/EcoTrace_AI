"""
EcoTrace AI - Memory Store

Lightweight persistent memory (JSON file-backed) that remembers:
  - previously uploaded suppliers and their structured records
  - previous carbon analyses (per-run snapshots)
  - previous recommendations made by the Optimization Agent

This is intentionally simple (no DB server) to keep the project easy to
run locally, but is structured so it could be swapped for SQLite/Postgres
later without changing the calling code.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import config


class MemoryStore:
    def __init__(self):
        self.path = config.MEMORY_DB_PATH
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            data = json.loads(self.path.read_text())
            data.setdefault("scenarios", [])
            return data
        return {"suppliers": {}, "analyses": [], "recommendations": [], "chat_history": [], "scenarios": []}

    def _save(self):
        self.path.write_text(json.dumps(self.data, default=str, indent=2))

    # --- Suppliers ---------------------------------------------------
    def upsert_supplier(self, record: dict[str, Any]):
        """Store/update a supplier record, keyed by supplier name."""
        name = record.get("supplier", "Unknown")
        existing = self.data["suppliers"].get(name, {})
        existing.update(record)
        existing["last_updated"] = datetime.now(timezone.utc).isoformat()
        self.data["suppliers"][name] = existing
        self._save()

    def get_all_suppliers(self) -> list[dict]:
        return list(self.data["suppliers"].values())

    def get_supplier(self, name: str) -> dict | None:
        return self.data["suppliers"].get(name)

    # --- Analyses ------------------------------------------------------
    def save_analysis(self, summary: dict[str, Any]) -> str:
        analysis_id = str(uuid.uuid4())[:8]
        entry = {
            "id": analysis_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **summary,
        }
        self.data["analyses"].append(entry)
        self._save()
        return analysis_id

    def get_latest_analysis(self) -> dict | None:
        if not self.data["analyses"]:
            return None
        return self.data["analyses"][-1]

    def get_all_analyses(self) -> list[dict]:
        return self.data["analyses"]

    # --- Recommendations -------------------------------------------------
    def save_recommendations(self, recs: list[dict[str, Any]]):
        timestamp = datetime.now(timezone.utc).isoformat()
        for r in recs:
            self.data["recommendations"].append({"timestamp": timestamp, **r})
        self._save()

    def get_all_recommendations(self) -> list[dict]:
        return self.data["recommendations"]

    # --- Scenarios (Scenario Simulator Agent) -----------------------------
    def save_scenario(self, scenario_record: dict[str, Any]) -> str:
        scenario_id = str(uuid.uuid4())[:8]
        entry = {
            "id": scenario_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **scenario_record,
        }
        self.data["scenarios"].append(entry)
        self._save()
        return scenario_id

    def get_all_scenarios(self) -> list[dict]:
        return self.data["scenarios"]

    # --- Chat history (for conversational continuity) -------------------
    def append_chat(self, role: str, content: str):
        self.data["chat_history"].append(
            {"role": role, "content": content, "timestamp": datetime.now(timezone.utc).isoformat()}
        )
        self._save()

    def get_chat_history(self, limit: int = 20) -> list[dict]:
        return self.data["chat_history"][-limit:]

    def clear_all(self):
        self.data = {"suppliers": {}, "analyses": [], "recommendations": [], "chat_history": [], "scenarios": []}
        self._save()
