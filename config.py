"""
EcoTrace AI - Central configuration

Loads environment variables, defines shared constants (emission factors,
model names, file paths) used across every agent and tool.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Paths -------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
STORAGE_DIR = ROOT_DIR / "storage"
REPORTS_DIR = ROOT_DIR / "reports"
UPLOADS_DIR = DATA_DIR / "uploads"

for d in [DATA_DIR, STORAGE_DIR, REPORTS_DIR, UPLOADS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

VECTOR_INDEX_PATH = STORAGE_DIR / "faiss_index.bin"
VECTOR_META_PATH = STORAGE_DIR / "faiss_meta.json"
MEMORY_DB_PATH = STORAGE_DIR / "memory.json"

# --- LLM config ----------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
CHAT_MODEL = os.getenv("ECOTRACE_CHAT_MODEL", "gemini-2.5-flash")
EMBED_MODEL = os.getenv("ECOTRACE_EMBED_MODEL", "gemini-embedding-001")
EMBED_DIM = 1536  # output_dimensionality requested from the embedding model

# --- Emission factors (approximate public reference values) -------------
# Units: kg CO2e per tonne-km, except where noted.
# Sources are illustrative/representative averages (e.g. UK DEFRA / EPA /
# ICCT style published factors), intended for estimation, not certification.
TRANSPORT_EMISSION_FACTORS = {
    "air": 0.602,        # kg CO2e per tonne-km - highest
    "road": 0.096,       # kg CO2e per tonne-km - truck/diesel average
    "sea": 0.016,        # kg CO2e per tonne-km - container ship
    "rail": 0.022,       # kg CO2e per tonne-km
    "local": 0.05,       # kg CO2e per tonne-km, short-haul local delivery
}

TRANSPORT_RELATIVE_TIER = {
    "air": "Very High",
    "road": "Medium",
    "sea": "Low",
    "rail": "Low",
    "local": "Very Low",
}

# Default product weight assumption (tonnes) when not specified in data,
# used to convert "per-shipment" rows into tonne-km if weight is missing.
DEFAULT_SHIPMENT_WEIGHT_TONNES = 1.0

# Category-level production emission factors (kg CO2e per kg of product),
# used as a rough proxy for "embedded" emissions when explicit factors
# aren't provided. Approximate / illustrative.
PRODUCT_CATEGORY_FACTORS = {
    "coffee beans": 15.0,
    "cocoa": 19.0,
    "cotton": 8.0,
    "textiles": 10.0,
    "electronics": 25.0,
    "steel": 2.3,
    "plastic": 3.5,
    "food (general)": 5.0,
    "default": 5.0,
}

# --- Certification score weights for Supplier Risk Agent (0-100 scale)
CERTIFICATION_BONUS = {
    "iso14001": 10,
    "fairtrade": 8,
    "organic": 6,
    "b corp": 10,
    "rainforest alliance": 7,
    "renewable energy certified": 10,
}

RAG_TOP_K = 5
CHUNK_SIZE_CHARS = 1200
CHUNK_OVERLAP_CHARS = 150

# --- Cost impact assumptions (Sustainability Goal Optimization Agent) ----
# Illustrative relative cost multipliers for shipping by transport mode,
# expressed as cost per tonne-km relative to road = 1.0. These are NOT
# real freight quotes - they encode the general industry pattern that air
# freight is fastest-but-priciest, sea is slowest-but-cheapest, and rail/
# local sit in between. Swap these for your own logistics quotes for
# audit-grade cost modeling.
TRANSPORT_COST_FACTORS = {
    "air": 4.5,
    "road": 1.0,
    "sea": 0.35,
    "rail": 0.55,
    "local": 0.6,
}

# Estimated one-time cost (as a % of the affected shipment's transport
# cost) of switching transport mode for a shipment - covers things like
# re-routing, new carrier contracts, packaging changes. Modeled as a
# fraction added to the recurring cost delta in the first year.
TRANSPORT_SWITCH_ONE_TIME_COST_PCT = 0.03

# Estimated cost delta (%) of moving sourcing to a regional/local supplier,
# applied to the *material* cost rather than transport cost - regional
# suppliers are often pricier per unit but cut transport drastically.
REGIONAL_SOURCING_COST_DELTA_PCT = 0.12

# Estimated cost delta (%) from consolidating shipments - savings from
# fewer trips, offset by potential warehousing/inventory carrying cost.
CONSOLIDATION_COST_DELTA_PCT = -0.04  # negative = net savings

# Estimated cost delta (%) from replacing a low-scoring supplier with a
# higher-sustainability-score one - certified/sustainable suppliers often
# carry a premium.
SUPPLIER_REPLACEMENT_COST_DELTA_PCT = 0.08

# Assumed material cost as a fraction of a shipment's total landed cost
# (used to estimate cost deltas for non-transport interventions like
# regional sourcing or supplier replacement, since we don't have explicit
# pricing data per shipment).
ASSUMED_MATERIAL_COST_SHARE = 0.7

# Assumed material cost baseline, expressed in the SAME relative cost-unit
# scale as TRANSPORT_COST_FACTORS (where road freight = 1.0 cost unit per
# tonne-km), so material-cost-driven interventions (regional sourcing,
# supplier replacement) can be combined/compared with transport-cost-driven
# interventions on a common absolute scale. This says: "the material cost
# of one tonne of average product is roughly equivalent to the transport
# cost of moving one tonne 500km by road" - a deliberately simple anchor,
# not a real pricing figure. Replace with actual unit economics for
# audit-grade cost modeling.
ASSUMED_MATERIAL_COST_UNITS_PER_TONNE = 500.0

# --- Operational (lead time) impact assumptions (Scenario Simulator Agent)
# Illustrative average transit speed implied per transport mode, expressed
# as days of lead time per 1,000 km of distance. These encode the general
# industry pattern (air is fast regardless of distance, sea/rail are slow
# but distance-insensitive past a fixed dock/port/terminal overhead) - NOT
# real carrier schedules. Swap for real transit-time tables for audit-grade
# lead-time projections.
TRANSPORT_LEADTIME_DAYS_PER_1000KM = {
    "air": 0.3,
    "road": 1.0,
    "sea": 2.2,
    "rail": 1.4,
    "local": 0.5,
}

# Fixed overhead days added on top of the distance-based estimate above,
# per transport mode (customs/port/terminal handling, consolidation at
# origin, etc.) - sea and rail carry meaningfully more fixed overhead than
# road/air/local.
TRANSPORT_LEADTIME_FIXED_DAYS = {
    "air": 1.0,
    "road": 0.5,
    "sea": 6.0,
    "rail": 3.0,
    "local": 0.2,
}

# Estimated one-time lead-time impact (days) of consolidating shipments -
# fewer, larger shipments slightly lengthen the gap between deliveries even
# though each individual shipment isn't slower in transit.
CONSOLIDATION_LEADTIME_DELTA_DAYS = 2.0

# Estimated lead-time impact (days) of supplier replacement / regional
# sourcing - generally improves (shortens) lead time since distance drops,
# modeled via the same distance-based formula above rather than a flat
# constant.
ROUTE_OPTIMIZATION_DISTANCE_REDUCTION_PCT = 0.08  # avg distance/time saved by smarter routing

# --- Recommendation engine thresholds (Scenario Simulator Agent) ---------
# Tiering thresholds for translating (emissions reduction %, cost impact %)
# into a plain-language recommendation label. Illustrative, tunable.
RECOMMENDATION_TIERS = {
    "highly_recommended": {"min_reduction_pct": 15, "max_cost_increase_pct": 5},
    "recommended": {"min_reduction_pct": 5, "max_cost_increase_pct": 15},
    "conditional": {"min_reduction_pct": 0, "max_cost_increase_pct": 1e9},
}
