"""Arabic / Franco / English normalization and the transliteration key."""
import normalize as n


def test_normalize_arabic_and_digits():
    assert n.normalize("أَسْعَار ٢٠٢٦ — Compound!") == "اسعار 2026 compound"
    assert n.normalize("مدينةٌ  جديدة") == "مدينه جديده"          # ة -> ه, diacritics gone
    assert n.normalize("إسكان آمن ى") == "اسكان امن ي"
    assert n.normalize("رقـــم") == "رقم"                        # tatweel
    assert n.normalize("۱۲۳") == "123"                           # Persian digits


def test_strip_generic_keeps_real_name_words():
    assert n.strip_generic("Compound Zed East Phase 2") == "zed east 2"
    assert n.strip_generic("كمبوند الربوة") == "الربوه"
    assert n.strip_generic("Hyde Park New Cairo") == "hyde park new cairo"   # park / new are part of names


def test_translit_meets_across_scripts():
    pairs = [("ماونتن فيو", "Mountain View"), ("بالم هيلز", "Palm Hills"), ("مدينتي", "Madinaty"),
             ("سوديك ويست", "SODIC West"), ("زد ايست", "ZED East"), ("هايد بارك", "Hyde Park"),
             ("الربوة", "Rabwa"), ("سيليستيا", "Celestia"), ("ريفان", "Rivan"), ("سولانا", "Solana"),
             ("جيران", "Jiran")]
    for ar, en in pairs:
        assert n.translit_key(ar) == n.translit_key(en), (ar, en, n.translit_key(ar), n.translit_key(en))
    assert n.translit_key("Mountain View") != n.translit_key("Palm Hills")
    assert n.translit_key("ma3ady") == n.translit_key("معادي")      # Franco-Arabic digit letters


def test_row_key_stable():
    assert n.row_key("ORA Developers ", "ZED  East", "New Cairo") == "oradevelopers|zedeast|New Cairo"
    assert n.is_arabic("زد") and not n.is_arabic("Zed")
