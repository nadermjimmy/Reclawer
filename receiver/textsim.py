"""Char n-gram TF-IDF similarity (scikit-learn), shared by the matcher, learning memory, noise filter and rule
suggestions. No external API, no embeddings.

Index(docs, use) fits TfidfVectorizer(analyzer='char_wb', ngram_range=(2, 4)) on the given texts. A fitted index
is cached in memory and under <MODELS_DIR>/tfidf-<use>-<hash of the corpus>.joblib, so it is refitted only when
the corpus changes (a master re-upload, new examples).
"""
import hashlib, os
import models

NGRAM = (2, 4)
_mem = {}


class Index:
    def __init__(self, docs, use, persist=True):
        self.docs = [d or "" for d in docs]
        self.use = use
        h = hashlib.sha1(("|".join(map(str, NGRAM)) + "\x00" + "\x00".join(self.docs)).encode()).hexdigest()[:16]
        self.key = (use, h)
        if self.key in _mem:
            self.vec, self.mat = _mem[self.key]
            return
        path = os.path.join(models.MODELS_DIR, f"tfidf-{use}-{h}.joblib")
        self.vec = self.mat = None
        if persist and os.path.exists(path):
            try:
                import joblib
                self.vec, self.mat = joblib.load(path)
            except Exception:
                self.vec = self.mat = None
        if self.vec is None and any(d.strip() for d in self.docs):
            from sklearn.feature_extraction.text import TfidfVectorizer
            self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=NGRAM, min_df=1, sublinear_tf=True)
            try:
                self.mat = self.vec.fit_transform(self.docs)
            except ValueError:  # nothing but stop characters
                self.vec = self.mat = None
            if persist and self.vec is not None:
                try:
                    import joblib
                    os.makedirs(models.MODELS_DIR, exist_ok=True)
                    joblib.dump((self.vec, self.mat), path)
                    _prune(use, keep=3)
                except OSError:
                    pass
        if len(_mem) > 32:
            _mem.clear()
        _mem[self.key] = (self.vec, self.mat)

    def sims(self, text):
        """Cosine similarity of `text` to every doc (list of floats)."""
        if self.vec is None or not (text or "").strip():
            return [0.0] * len(self.docs)
        q = self.vec.transform([text])
        return (self.mat @ q.T).toarray().ravel().tolist()

    def top(self, text, k=10, floor=0.0):
        s = self.sims(text)
        order = sorted(range(len(s)), key=lambda i: -s[i])[:k]
        return [(i, s[i]) for i in order if s[i] > floor]


def _prune(use, keep=3):
    d = models.MODELS_DIR
    files = sorted((os.path.getmtime(os.path.join(d, n)), n) for n in os.listdir(d)
                   if n.startswith(f"tfidf-{use}-") and n.endswith(".joblib"))
    for _, n in files[:-keep]:
        try:
            os.remove(os.path.join(d, n))
        except OSError:
            pass


def pair(a, b):
    """Cosine of two short strings (fits a tiny vectorizer on the pair; for one-off comparisons)."""
    if not (a or "").strip() or not (b or "").strip():
        return 0.0
    from sklearn.feature_extraction.text import TfidfVectorizer
    try:
        m = TfidfVectorizer(analyzer="char_wb", ngram_range=NGRAM).fit_transform([a, b])
    except ValueError:
        return 0.0
    return float((m[0] @ m[1].T).toarray()[0, 0])
