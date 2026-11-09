"""Versioned model storage under /data/models/ (matcher, noise filter).

Layout: <MODELS_DIR>/<kind>-<version>/model.pkl + meta.json, and <MODELS_DIR>/<kind>-current holding the promoted
version's name. Nothing is promoted implicitly: train scripts save a version; retrain.py (or --promote) promotes it.
"""
import json, os, shutil, time
import db

MODELS_DIR = os.getenv("MODELS_DIR") or os.path.join(db.DATA_DIR, "models")
_cache = {}


def _dir(kind, ver):
    return os.path.join(MODELS_DIR, f"{kind}-{ver}")


def new_version():
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + f"-{int(time.time() * 1000) % 1000:03d}"


def save(kind, ver, model, meta):
    import joblib
    d = _dir(kind, ver)
    os.makedirs(d, exist_ok=True)
    joblib.dump(model, os.path.join(d, "model.pkl"))
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump(dict(meta, kind=kind, version=ver), f, indent=1, default=str)
    return d


def meta(kind, ver):
    try:
        with open(os.path.join(_dir(kind, ver), "meta.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def current_version(kind):
    try:
        with open(os.path.join(MODELS_DIR, f"{kind}-current")) as f:
            ver = f.read().strip()
        return ver if os.path.isdir(_dir(kind, ver)) else None
    except OSError:
        return None


def promote(kind, ver):
    os.makedirs(MODELS_DIR, exist_ok=True)
    tmp = os.path.join(MODELS_DIR, f".{kind}-current.tmp")
    with open(tmp, "w") as f:
        f.write(ver)
    os.replace(tmp, os.path.join(MODELS_DIR, f"{kind}-current"))


def load(kind, ver):
    if not ver:
        return None
    key = (kind, ver)
    if key not in _cache:
        import joblib
        try:
            _cache[key] = joblib.load(os.path.join(_dir(kind, ver), "model.pkl"))
        except Exception:
            return None
    return _cache[key]


def load_current(kind):
    """(model, version) of the promoted model, or (None, None)."""
    ver = current_version(kind)
    m = load(kind, ver)
    return (m, ver) if m is not None else (None, None)


def versions(kind):
    if not os.path.isdir(MODELS_DIR):
        return []
    out = [n[len(kind) + 1:] for n in os.listdir(MODELS_DIR) if n.startswith(kind + "-") and n != f"{kind}-current"
           and os.path.isdir(os.path.join(MODELS_DIR, n))]
    return sorted(out)


def prune(kind, keep=5):
    """Drop old versions, never the current one."""
    cur = current_version(kind)
    for ver in versions(kind)[:-keep]:
        if ver != cur:
            shutil.rmtree(_dir(kind, ver), ignore_errors=True)
