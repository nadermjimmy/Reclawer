"""The Byit app catalog: every Egypt developer (companies.csv) and every project with its developer (projects.csv),
uploaded on the Master page. It is the skeleton the accuracy gate checks suggestions against:
- which developer a WhatsApp group belongs to (when the team hasn't assigned one),
- which developer owns a project name ('Clavo Mall' is SGH's),
- whether a developer is an Egypt developer at all (a 'Byit App / X' group whose X isn't here is a UAE group).
"""
import csv, io, json, re, time, collections
import db, names, normalize


def import_csv(data):
    """Store companies.csv or projects.csv (detected from the header). Returns {kind, rows}."""
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise ValueError("The file has no rows")
    cols = {c.strip().lower() for c in rows[0]}
    now = int(time.time())
    with db.connect() as c:
        if {"englishname", "arabicname"} <= cols:
            c.execute("DELETE FROM catalog_companies")
            for r in rows:
                g = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
                if not g.get("englishname") and not g.get("arabicname"):
                    continue
                c.execute("INSERT INTO catalog_companies(ext_id,english,arabic,type,imported_at) VALUES(?,?,?,?,?)",
                          (g.get("#") or g.get("id"), g.get("englishname"), g.get("arabicname"), g.get("type"), now))
            kind = "companies"
        elif {"name", "company"} <= cols:
            c.execute("DELETE FROM catalog_projects")
            for r in rows:
                g = {k.strip().lower(): (v or "").strip() for k, v in r.items() if k}
                if not g.get("name"):
                    continue
                c.execute("INSERT INTO catalog_projects(ext_id,name,company,company_ext_id,extra_json,imported_at) "
                          "VALUES(?,?,?,?,?,?)", (g.get("#"), g["name"], g.get("company"), g.get("company id"),
                                                 json.dumps(g, ensure_ascii=False), now))
            kind = "projects"
        else:
            raise ValueError("Expected the companies export (englishName, arabicName) or the projects export "
                             "(Name, Company)")
        n = c.execute(f"SELECT COUNT(*) FROM catalog_{kind}").fetchone()[0]
    _cache.clear()
    return {"kind": kind, "rows": n}


def counts(c):
    return {"companies": c.execute("SELECT COUNT(*) FROM catalog_companies").fetchone()[0],
            "projects": c.execute("SELECT COUNT(*) FROM catalog_projects").fetchone()[0]}


class TokenIndex:
    """Names sharing a distinctive word (or the whole key) with a query, so a lookup doesn't compare every name."""

    def __init__(self, items):
        self.items = list(items)
        self.by_tok, self.by_key = collections.defaultdict(set), collections.defaultdict(set)
        for i, n in enumerate(self.items):
            for v in names.variants(n):
                self.by_key[names.key(v)].add(i)
                for t in names.distinct(v):
                    self.by_tok[t].add(i)
                for t in normalize.translit_key(v).split():
                    self.by_tok["~" + t].add(i)
        self.vocab = [t for t in self.by_tok if not t.startswith("~")]

    def candidates(self, q):
        from rapidfuzz import fuzz, process
        out = set()
        for v in names.variants(q):
            out |= self.by_key.get(names.key(v), set())
            for t in names.distinct(v):
                for m, _, _ in process.extract(t, self.vocab, scorer=fuzz.ratio, score_cutoff=92, limit=20):
                    out |= self.by_tok[m]
            if normalize.is_arabic(v):
                for t in normalize.translit_key(v).split():
                    out |= self.by_tok.get("~" + t, set())
        return out


class Catalog:
    def __init__(self, c):
        self.companies = [dict(r) for r in c.execute("SELECT english, arabic FROM catalog_companies")]
        self.projects = [(r["name"], r["company"] or "") for r in c.execute("SELECT name, company FROM catalog_projects")]
        self.co_names = [(n, co["english"] or co["arabic"]) for co in self.companies
                         for n in (co["english"], co["arabic"]) if n]
        self.pidx = TokenIndex(n for n, _ in self.projects)
        self.cidx = TokenIndex(n for n, _ in self.co_names)
        self.dubai_names = [normalize.normalize(n) for n, _ in self.co_names
                            if "dubai" in normalize.normalize(n) or "دبي" in normalize.normalize(n)]

    @property
    def empty(self):
        return not self.companies and not self.projects

    def company(self, raw):
        """(catalog company, score) for a developer name, or (None, 0)."""
        if not raw or not raw.strip():
            return None, 0.0
        k = names.key(raw)
        if k in names.ALIASES:
            return names.ALIASES[k], 1.0
        best = (0.0, None)
        for i in self.cidx.candidates(raw):
            n, co = self.co_names[i]
            s = names.sim(raw, n, names.CO_GENERIC, subset_ok=True)
            if s > best[0]:
                best = (s, co)
        if best[0] >= 0.9:
            return best[1], best[0]
        q = normalize.normalize(raw)
        if len(k) >= 3:  # a group named after one of its projects: 'GDG' -> 'The One (GDG)'
            for name, pco in self.projects:
                if re.search(r"(^|[^\w])" + re.escape(q) + r"([^\w]|$)", normalize.normalize(name)):
                    return pco, 0.9
        return None, best[0]

    def is_company(self, raw):
        return self.company(raw)[0] is not None

    def project(self, raw, company=None, strong=False):
        """(score, catalog project name, its company) of the best match, or None."""
        best = None
        for i in self.pidx.candidates(raw):
            name, co = self.projects[i]
            s = names.sim(raw, name, strong=strong)
            if s < 0.9:
                continue
            if company and names.same_company(co, company):
                s += 0.03
            if best is None or s > best[0]:
                best = (s, name, co)
        return best


_cache = {}


def get(c):
    sig = (c.execute("SELECT COUNT(*), MAX(imported_at) FROM catalog_companies").fetchone()[:],
           c.execute("SELECT COUNT(*), MAX(imported_at) FROM catalog_projects").fetchone()[:])
    if _cache.get("sig") != sig:
        _cache.update(sig=sig, cat=Catalog(c))
    return _cache["cat"]


def group_developer(c, jid, name=None, cat=None):
    """(developer, source) for a WhatsApp group: the team's assignment first ('team'), else the catalog company the
    group name points to ('catalog'), else (None, None)."""
    if not jid:
        return None, None
    p = c.execute("SELECT developer, source FROM chat_profiles WHERE jid=?", (jid,)).fetchone()
    if p and p["source"] == "team" and (p["developer"] or "").strip():
        return p["developer"].strip(), "team"
    if name is None:
        g = c.execute("SELECT name FROM groups WHERE jid=?", (jid,)).fetchone()
        name = g[0] if g else ""
    cat = cat or get(c)
    raw = names.group_developer_text(name or "")
    co, s = cat.company(raw) if raw and not cat.empty else (None, 0)
    if co and s >= 0.9 and not names.uae_style_name(name):
        return co, "catalog"
    return None, None
