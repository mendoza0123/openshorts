import os
import sys

# Make the repo root importable so tests can import the app modules directly.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Tests always run the app in self-host (BYOK) mode. app.py freezes
# BILLING_ENABLED at import time and load_dotenv never overrides an existing
# variable, so this must be set here — before any test module imports app — or
# the suite's behavior would depend on the developer's personal .env.
os.environ["BILLING_ENABLED"] = "0"
# Same reason for the clip store: a developer's .env pointing NEON_DATABASE_URL
# at a real database must not receive the rows of the suite's fake jobs. Empty
# means off, and load_dotenv never overrides a variable that is already set.
os.environ["NEON_DATABASE_URL"] = ""
# And for the server password: a developer's own OPENSHORTS_API_TOKEN would put
# every API test behind a 401. Tests that need it set app.API_TOKEN themselves.
os.environ["OPENSHORTS_API_TOKEN"] = ""
# Generation defaults a self-hoster may keep in .env (AUTO_HOOK=1, a 15-40 s band,
# 4 clips) would change what every pipeline test expects. Empty reads as "unset"
# in clip_selection, main.py and layout_picker.
for _name in ("AUTO_HOOK", "AUTO_LAYOUT", "CLIP_MIN_SECONDS", "CLIP_MAX_SECONDS",
              "CLIP_TARGET_MIN", "CLIP_TARGET_MAX"):
    os.environ[_name] = ""
