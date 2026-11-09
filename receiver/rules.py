"""Owner corrections (owner_rules_<market>.json) enforced in code, independent of what Claude proposes."""
import json, os, re

MARKET = os.getenv("MARKET", "eg").lower()
with open(os.path.join(os.path.dirname(__file__), f"owner_rules_{MARKET}.json"), encoding="utf-8") as f:
    RULES = json.load(f)

MARKET_NAME = RULES["market_name"]            # "Egypt" / "the UAE"
MARKET_SHORT = RULES["market"]                # "EG" / "UAE"
CURRENCY = RULES["currency"]                  # "EGP" / "AED"
CURRENCY_RE = re.compile(RULES["currency_pattern"], re.I)
METRIC = RULES["price_area_metric"]           # "sq_m" / "sq_ft"
METRIC_LABEL = "sq m" if METRIC == "sq_m" else "sq ft"
