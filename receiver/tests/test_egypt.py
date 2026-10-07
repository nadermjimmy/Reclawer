import os, subprocess, sys, textwrap

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(code):
    env = dict(os.environ, MARKET="eg")
    out = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=HERE, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_egypt_rules_and_price_per_sqm():
    out = run("""
        import rules, build, parse_files
        assert rules.CURRENCY == "EGP" and rules.METRIC == "sq_m"
        assert rules.why_not_a_project("New Cairo") and rules.why_not_a_project("Phase 2")
        assert rules.why_not_a_project("Mountain View iCity") is None
        assert rules.CURRENCY_RE.search("السعر بالجنيه") and rules.CURRENCY_RE.search("Price EGP")
        v, inputs = build.price_per_area(5000000.0, "EGP", {"value": 100.0, "unit": "sq_m", "label": "BUA", "raw": "100"})
        assert v == 50000.0 and "area_sq_m" in inputs
        v, _ = build.price_per_area(1076391.04167, "EGP", {"value": 1076.39104167, "unit": "sq_ft", "label": "A", "raw": "x"})
        assert round(v) == 10764
        assert build.parse_number("2,500,000 EGP") == 2500000 and build.parse_number("2.5M") is None
        assert parse_files.role_of("رقم الوحدة") == "unit_code" and parse_files.role_of("السعر") == "price"
        import mapping, facts
        assert "Egypt" in mapping.SYSTEM and "Brabus" not in mapping.SYSTEM and "Egypt" in facts.SYSTEM
        print("ok")
    """)
    assert out.strip() == "ok"
