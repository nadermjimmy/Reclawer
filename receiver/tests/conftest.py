import os, sys, tempfile

TMP = tempfile.mkdtemp()
os.environ.update(DB_PATH=os.path.join(TMP, "test.db"), WEBHOOK_TOKEN="t", APP_PASSWORD="pw",
                  EVOLUTION_URL="http://evolution.test", EVOLUTION_API_KEY="k",
                  EVOLUTION_INSTANCE="byit-ops", MARKET="uae",
                  INGEST_MODE="live", INGEST_DAYS="0", INGEST_POLL_SECONDS="0", CLAUDE_FALLBACKS="0")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
