"""Owner corrections (owner_rules.json) enforced in code, independent of what Claude proposes."""
import json, os, re

with open(os.path.join(os.path.dirname(__file__), "owner_rules.json"), encoding="utf-8") as f:
    RULES = json.load(f)


def why_not_a_project(name):
    """Reason the label can't be a project (location / instruction / tower), or None."""
    n = (name or "").strip()
    low = n.lower()
    if not n:
        return "empty project name"
    if any(low == x.lower() for x in RULES["not_project_names"]):
        return f"'{n}' is listed by the owner as not a project"
    if any(low == x.lower() for x in RULES["locations"]):
        return f"'{n}' is a location, not a project"
    for pat in RULES["not_project_patterns"]:
        if re.search(pat, low):
            return f"'{n}' looks like a tower/phase label or an instruction, not a project"
    return None


def parent_group(name):
    low = (name or "").lower()
    return next((g["pattern"] for g in RULES["one_parent_groups"] if g["pattern"] in low), None)


def phase_limit(project_name):
    low = (project_name or "").lower()
    return next((v for k, v in RULES["phase_limits"].items() if k in low), None)


def phase_number(label):
    m = re.search(r"(\d+)", label or "")
    return int(m.group(1)) if m else None


def image_blocked(text):
    low = (text or "").lower()
    return next((t for t in RULES["image_blocklist_terms"] if t in low), None)
