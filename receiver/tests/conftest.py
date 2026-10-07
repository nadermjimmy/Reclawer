import os, sys, tempfile

TMP = tempfile.mkdtemp()
os.environ.update(DB_PATH=os.path.join(TMP, "test.db"), WEBHOOK_TOKEN="t", APP_PASSWORD="pw",
                  EVOLUTION_URL="http://evolution.test", EVOLUTION_API_KEY="k",
                  EVOLUTION_INSTANCE="byit-ops", MARKET="uae")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
