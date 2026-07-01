"""
EcoTrace AI - Web Search Tool

Optional tool the Planner/Optimization agents can call to look up
public information (e.g. "is Supplier X certified B Corp", "average
emission factor for rail freight in the EU"). Implemented against the
Serper.dev API (Google Search API) since it's simple and has a free tier.
If no SERPER_API_KEY is set, the tool degrades gracefully and tells the
agent web search is unavailable, rather than crashing the app.

To enable: get a free key at https://serper.dev and set SERPER_API_KEY
in your .env file.
"""
import os

import requests

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL = "https://google.serper.dev/search"


def web_search(query: str, num_results: int = 5) -> dict:
    if not SERPER_API_KEY:
        return {
            "available": False,
            "message": (
                "Web search is not configured. Set SERPER_API_KEY in .env "
                "to enable live lookups (e.g. from https://serper.dev). "
                "Proceeding using internal knowledge and uploaded documents only."
            ),
        }

    try:
        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num_results},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        organic = data.get("organic", [])[:num_results]
        results = [
            {
                "title": r.get("title"),
                "snippet": r.get("snippet"),
                "link": r.get("link"),
            }
            for r in organic
        ]
        return {"available": True, "query": query, "results": results}
    except Exception as e:  # noqa: BLE001
        return {"available": False, "message": f"Web search failed: {e}"}


WEB_SEARCH_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for current public information relevant to "
            "sustainability, e.g. supplier certifications, regional emission "
            "factors, or recent regulations. Only use when uploaded documents "
            "and internal estimates are insufficient to answer the question."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
            },
            "required": ["query"],
        },
    },
}
