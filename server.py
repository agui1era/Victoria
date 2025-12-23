from flask import Flask, request
from flask_cors import CORS
from pymongo import MongoClient
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Enable CORS for all routes
CORS(app, resources={r"/*": {"origins": "*"}})

# ======================
# CONFIG
# ======================
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB_NAME", "victoria")

MONGO_DAILY_COLLECTION_NAME = "victoria_daily_reports"

API_KEY = os.getenv("VICTORIA_APIKEY", "aOhSfdBLPFEXC2HJlXPpT8AQ5wKVc")

# ======================
# MONGO
# ======================

mongo = MongoClient(MONGO_URI)
db = mongo[MONGO_DB]
daily_col = db[MONGO_DAILY_COLLECTION_NAME]

# ======================
# HELPERS
# ======================

def is_apikey_valid(req) -> bool:
    return req.args.get("apikey") == API_KEY

def sanitize(text: str) -> str:
    if not text:
        return "No information available."
    return (
        text.replace("<", "")
            .replace(">", "")
            .replace("&", " and ")
            .replace('"', "")
            .replace("'", "")
            .strip()
    )

# ======================
# ENDPOINTS
# ======================

@app.get("/report/blocks/3h")
def report_blocks_3h():
    if not is_apikey_valid(request):
        return {"error": "Invalid apikey"}, 403

    # date=YYYY-MM-DD (UTC)
    day = request.args.get("date")
    granularity = request.args.get("granularity", "3h") # '3h' or 'detailed'
    
    if not day:
        day = datetime.now(timezone.utc).date().isoformat()

    doc = daily_col.find_one({"date": day})
    items = []
    
    field_name = "blocks_detailed" if granularity == "detailed" else "blocks"

    if doc and field_name in doc:
        blocks = doc[field_name]
        # blocks is a dict: {"00": {...}} or {"00:10": {...}}
        
        for key in sorted(blocks.keys()):
            b_data = blocks[key]
            
            # Construct ISO timestamp
            if len(key) == 2: # "00", "03"
                block_ts_iso = f"{day}T{key}:00:00+00:00"
            elif len(key) == 5: # "09:10"
                block_ts_iso = f"{day}T{key}:00+00:00"
            else:
                block_ts_iso = f"{day}T00:00:00+00:00" # Fallback

            items.append({
                "block": block_ts_iso,
                "texto": sanitize(b_data.get("text", "")),
                "score": b_data.get("score", 0),
                "events_hash": f"count:{b_data.get('events_count', 0)}",
                "events_detail": b_data.get("events_detail", []), 
                "is_current": (b_data.get("status") == "active")
            })

    return {
        "day": day, 
        "granularity": granularity,
        "daily_summary": doc.get("daily_summary", "Sin resumen disponible."), 
        "daily_score": doc.get("daily_score", 0.0),
        "items": items
    }

# ======================
# MAIN
# ======================

if __name__ == "__main__":
    print("Victoria Server running on port 8888 🦊")
    app.run(host="0.0.0.0", port=8888)