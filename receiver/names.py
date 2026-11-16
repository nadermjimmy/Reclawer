"""Strict project / developer name comparison, tuned on the team's own data (sanity check of 15 Nov 2026).

Rules (each one fixed a real false match):
- the same name in another spelling is the same project ('Assl' / 'ASSL'), and a name in brackets is an alternative
  name ('The 101 (Motion Collection)' also answers to 'Motion Collection');
- descriptive words (Mall, Residence, Park, Business, North, Capital, numbers …) never make two names match on their
  own, and two names with DIFFERENT qualifiers are different projects ('Elite Mall' / 'Elite West', 'North Edge' /
  'Capital Edge');
- the distinctive words must agree both ways; one name inside the other ('Assl New Cairo' / 'Assl') only counts when
  the caller allows it (e.g. among the group's own developer's rows);
- Arabic vs Latin compares transliteration keys; within one script there is no sound-alike matching.
"""
import functools, re
import normalize

N = normalize.normalize

DESCRIPTIVE = {"business", "complex", "mall", "tower", "towers", "residence", "residences", "city", "park", "hub",
               "plaza", "walk", "gate", "gates", "gardens", "garden", "heights", "medical", "commercial", "admin",
               "administrative", "square", "squares", "center", "centre", "compound", "villas", "villa", "homes",
               "home", "district", "new", "cairo", "capital", "zayed", "october", "the", "x", "one", "views", "view",
               "apartments", "apartment", "building", "point", "avenue", "hills", "bay", "lake", "lakes", "strip",
               "club", "resort", "beach", "coast", "north", "west", "east", "south", "phase", "icon", "hotel"}
CO_GENERIC = {"development", "developments", "developers", "developer", "group", "properties", "property", "misr",
              "egypt", "holding", "holdings", "real", "estate", "investment", "investments", "company", "co", "for",
              "urban", "housing", "dev", "al", "el"}
COMMON_CO_WORDS = {"hills", "capital", "city", "homes", "gate", "gates", "view", "edge", "park", "land", "towers",
                   "square", "squares", "golden", "green", "star", "elite", "prime", "royal", "smart", "modern",
                   "new", "west", "east", "north", "south", "egyptian", "arab", "arabia", "international"}
# short names and abbreviations the groups use for developers
ALIASES = {"mv": "Mountain View", "mnhd": "Madinet Masr", "madinetnasr": "Madinet Masr"}
CO_EQUIV = [{"mnhd", "madinetmasr", "madinetnasr"}, {"mv", "mountainview"}, {"alahlysabbour", "sabbour", "mintsabbour"}]


def key(s):
    return normalize.compact(s)


def _ratio(a, b):
    from rapidfuzz import fuzz
    return fuzz.ratio(a, b)


@functools.lru_cache(maxsize=200000)
def distinct(name, extra=()):
    return tuple(t for t in (normalize.strip_generic(name) or N(name)).split() if t not in DESCRIPTIVE and t not in extra)


@functools.lru_cache(maxsize=200000)
def words(name, extra=()):
    return tuple(t for t in (normalize.strip_generic(name) or N(name)).split() if t not in extra)


@functools.lru_cache(maxsize=200000)
def variants(name):
    """'The 101 ( Motion Collection )' -> ('The 101', 'Motion Collection')."""
    main = re.sub(r"\([^)]*\)", " ", name or "").strip()
    return tuple(v for v in [main, *re.findall(r"\(([^)]*)\)", name or "")] if v.strip())


def _tok_eq(t, u):
    if t == u:
        return True
    if t.isdigit() or u.isdigit():
        return False
    return _ratio(t, u) >= 92


def _one(a, b, extra, subset_ok, strong):
    ka, kb = key(a), key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    da, db = distinct(a, extra), distinct(b, extra)
    if normalize.is_arabic(a) != normalize.is_arabic(b):  # Arabic vs Latin: whole-name consonant skeletons
        ta, tb = normalize.translit_key(" ".join(words(a, extra))), normalize.translit_key(" ".join(words(b, extra)))
        return 0.93 if ta and ta == tb and len(ta.replace(" ", "")) >= 4 else 0.0
    if not da or not db:
        return _ratio(ka, kb) / 100 * 0.9
    qa = [t for t in words(a, extra) if t not in da]
    qb = [t for t in words(b, extra) if t not in db]
    if qa and qb and not any(_tok_eq(t, u) for t in qa for u in qb):
        return 0.0  # different qualifiers: 'Elite Mall' / 'Elite West'
    cov_a = all(any(_tok_eq(t, u) for u in db) for t in da)
    cov_b = all(any(_tok_eq(u, t) for t in da) for u in db)
    if not any(len(t) >= 3 and not t.isdigit() and any(_tok_eq(t, u) for u in db) for t in da):
        return 0.0
    if cov_a and cov_b:
        if strong and min(len(da), len(db)) < 2:
            return 0.0  # one shared word ('Seven', 'Central') isn't enough to name another developer's project
        return 0.97
    if subset_ok and (cov_a or cov_b):
        return 0.9
    return 0.0


def sim(a, b, extra=(), subset_ok=False, strong=False):
    """0..1 similarity of two project (or developer) names; >= 0.9 means the same."""
    extra = tuple(extra)
    return max((_one(x, y, extra, subset_ok, strong) for x in variants(a) for y in variants(b)), default=0.0)


def _co_words(x):
    return {t for t in re.sub(r"[()]", " ", N(x)).split()
            if t not in CO_GENERIC and t not in COMMON_CO_WORDS and len(t) >= 5}


def same_company(a, b):
    """Two developer names refer to the same company ('MNHD' / 'Madinet Masr', 'Orascom(Gouna)' / 'Orascom')."""
    ka, kb = key(a), key(b)
    if not (ka and kb):
        return False
    if ka == kb or _ratio(ka, kb) >= 88 or (min(len(ka), len(kb)) >= 3 and (ka in kb or kb in ka)):
        return True
    if any(ka in g and kb in g for g in CO_EQUIV):
        return True
    return bool(_co_words(a) & _co_words(b))


def developer_words(*names):
    """Words of developer names, to exclude from project matching ('Prime Nova' vs 'Prime Plaza' in Prime's group)."""
    out = set()
    for n in names:
        out |= _co_words(n or "")
    return tuple(sorted(out))


NOISE_WORDS = re.compile(r"\b(byit|app|ahya|nawy|partners?|updates?|team|sales|brokers?|official|and)\b|"
                         r"[^\w\s&/]|\d{3,}", re.I)


def group_developer_text(name):
    """The developer part of a WhatsApp group name: 'Hyde Park & Byit App' -> 'hyde park',
    'Byit App / Samana Developers' -> 'samana developers'."""
    n = re.sub(r"\([^)]*\)", " ", name or "")
    for pat in (r"^\s*byit\s*(?:app)?\s*(?:[/|\-×🤝&]|\s+x\s+)+\s*(.+)$",
                r"^(.+?)\s*(?:&|/|\||×|🤝|-|\s+x\s+)\s*(?:&\s*)?byit", r"^(.+?)\s+updates?\b"):
        m = re.search(pat, n, re.I)
        if m:
            n = m.group(1)
            break
    return " ".join(N(NOISE_WORDS.sub(" ", n)).split())


def uae_style_name(name):
    """The UAE groups are named 'Byit App / X' (Egypt groups: 'X & Byit App')."""
    return bool(re.match(r"^\s*byit\s*(app)?\s*[/|]", name or "", re.I))
