import os, subprocess, sys, textwrap

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run(code):
    env = dict(os.environ, MARKET="eg")
    out = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], cwd=HERE, env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_egypt_rules():
    out = run("""
        import rules, parse_files
        assert rules.CURRENCY == "EGP" and rules.METRIC == "sq_m"
        assert rules.CURRENCY_RE.search("السعر بالجنيه") and rules.CURRENCY_RE.search("Price EGP")
        assert parse_files.role_of("رقم الوحدة") == "unit_code" and parse_files.role_of("السعر") == "price"
        print("ok")
    """)
    assert out.strip() == "ok"
