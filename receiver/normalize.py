"""Arabic / English / Franco-Arabic normalization for matching and stable keys (Egypt).

- normalize(text): one comparable form. Arabic: alef forms -> ا, ى -> ي, ة -> ه, ؤ/ئ -> ء, diacritics and tatweel
  removed; Arabic-Indic and Persian digits -> ASCII; Latin lowercased; punctuation collapsed to single spaces.
- strip_generic(text): normalize, then drop words that don't identify a project (compound, phase, residence,
  tower, el/al, مشروع, كمبوند, فيز …).
- translit_key(text): a deterministic Latin consonant skeleton for BOTH scripts, so 'ماونتن فيو' and 'Mountain View'
  meet: Arabic letters map to Latin (leading 'ال' and final ة dropped), then vowels, w/y and doubled letters are
  dropped and sounds the scripts write differently are folded (p->b, v->f, z->s, soft c->s, x->ks). Lossy by
  design; used only for comparison, never shown.
- row_key(developer, project, sheet): the stable identity of a master row that survives a re-upload.

Pure functions, no dependencies beyond the standard library.
"""
import re, unicodedata

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭ]")
_TATWEEL = "ـ"
_AR_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "ء", "ئ": "ء",
                          "گ": "ك", "ڤ": "ف", "پ": "ب", "چ": "ج"})
_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")

# words that describe a project type, not its name (both scripts, after normalize). 'park', 'mall', 'new', 'misr'
# are left out on purpose: they're part of real names (Hyde Park, New Capital, Misr Italia).
GENERIC = {
    "compound", "compounds", "phase", "phases", "residence", "residences", "residential", "tower", "towers",
    "project", "the", "el", "al", "by", "development", "developments", "developer", "developers", "properties",
    "property", "real", "estate",
    "مشروع", "كمبوند", "كومباوند", "فيز", "فاز", "المرحله", "مرحله", "ريزيدنس", "تاور", "برج", "ال",
}

# Arabic -> Latin, the way Egyptian developers spell their names in English
_AR2LAT = {
    "ا": "a", "ء": "", "ب": "b", "ت": "t", "ث": "s", "ج": "g", "ح": "h", "خ": "kh", "د": "d", "ذ": "z", "ر": "r",
    "ز": "z", "س": "s", "ش": "sh", "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "ع": "a", "غ": "gh", "ف": "f",
    "ق": "k", "ك": "k", "ل": "l", "م": "m", "ن": "n", "ه": "h", "و": "w", "ي": "y",
}
# Franco-Arabic digits used as letters (only inside words that also contain Latin letters)
_FRANCO = {"2": "a", "3": "a", "5": "kh", "7": "h", "8": "gh", "9": "k"}


def _nfkc(text):
    return unicodedata.normalize("NFKC", str(text or ""))


def normalize(text):
    s = _nfkc(text).translate(_DIGITS)
    s = _DIACRITICS.sub("", s).replace(_TATWEEL, "").translate(_AR_FOLD).lower()
    s = re.sub(r"[^\w\s]|_", " ", s)
    return " ".join(s.split())


def strip_generic(text):
    words = [w for w in normalize(text).split() if w not in GENERIC]
    # a leading Arabic definite article glued to the word ('الربوه' -> 'ربوه') is kept: it's part of many names
    return " ".join(words)


def is_arabic(text):
    return bool(re.search(r"[؀-ۿ]", str(text or "")))


def _to_latin(word):
    if is_arabic(word):
        if word.startswith("ال") and len(word) > 3:   # definite article: 'الربوه' -> 'ربوه'
            word = word[2:]
        if word.endswith("ه") and len(word) > 2:       # ta marbuta (ة, folded to ه) is a vowel sound
            word = word[:-1]
        return "".join(_AR2LAT.get(ch, ch if ch.isascii() else "") for ch in word)
    if re.search(r"[a-z]", word) and re.search(r"[235789]", word):  # Franco: 'ma3ady', '7ayah'
        return "".join(_FRANCO.get(ch, ch) for ch in word)
    return word


def _skeleton(word):
    w = word.lower()
    w = w.replace("ph", "f").replace("ck", "k").replace("qu", "k").replace("x", "ks").replace("p", "b")
    w = re.sub(r"c(?=[eiy])", "s", w)  # soft c: 'Celestia'
    w = w.replace("v", "f").replace("q", "k").replace("c", "k").replace("j", "g").replace("th", "s")
    w = w.replace("z", "s")  # Arabic writes the English s/z sound with ز or س interchangeably ('هيلز' = Hills)
    w = re.sub(r"[aeiouwy]", "", w)
    w = re.sub(r"(.)\1+", r"\1", w)  # doubled letters
    return w


def translit_key(text):
    """'ماونتن فيو' -> 'mntn f'; 'Mountain View' -> 'mntn f'; 'بالم هيلز' / 'Palm Hills' -> 'blm hls'."""
    out = []
    for word in normalize(text).split():
        if word in GENERIC:
            continue
        sk = _skeleton(_to_latin(word))
        if sk:
            out.append(sk)
    return " ".join(out)


def compact(text):
    """normalize() without spaces: the form master project_key comparisons use."""
    return normalize(text).replace(" ", "")


def row_key(developer, project, sheet):
    """Stable identity of a master row across re-uploads: normalized developer | project | sheet."""
    return f"{compact(developer)}|{compact(project)}|{(sheet or '').strip()}"
