import os
import time
import json
import logging
import random
import requests
import re
from datetime import datetime, timezone, timedelta
from difflib import SequenceMatcher
from dotenv import load_dotenv
from pymongo import MongoClient

# ======================
# CONFIG & SETUP
# ======================

load_dotenv()

# Logger setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("VictoriaWorker")

# Environment Variables
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB_NAME", "victoria")
MONGO_EVENTS_COLLECTION = os.getenv("MONGO_EVENTS_COLLECTION", "events") # Default raw events
MONGO_CACHE_COLLECTION = "victoria_cache"
MONGO_HISTORY_COLLECTION = "victoria_cache_history"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

# Interval
CHECK_INTERVAL = int(os.getenv("CALC_INTERVAL_SECONDS", "60"))
BLOCK_DURATION_MINUTES = int(os.getenv("BLOCK_DURATION_MINUTES", "180")) # Default 3 hours

# Analysis Prompt
PROMPT_ANALYSIS = """
Analiza los siguientes eventos del sistema y genera un reporte conciso.
Si hay errores criticos, destacalos.
Si todo esta normal, indicalo brevemente.
Responde en formato JSON: {"score": <0-10 de gravedad>, "text": "<resumen>"}
"""

# ======================
# MONGO CONNECTION
# ======================

try:
    mongo = MongoClient(MONGO_URI)
    db = mongo[MONGO_DB]
    col_events = db[MONGO_EVENTS_COLLECTION]
    col_cache = db[MONGO_CACHE_COLLECTION]
    col_history = db[MONGO_HISTORY_COLLECTION]
    logger.info(f"Connected to MongoDB: {MONGO_DB}")
except Exception as e:
    logger.error(f"Failed to connect to MongoDB: {e}")
    exit(1)

# ======================
# HELPERS (Migrated)
# ======================

def with_retries(request_fn, max_attempts=3, base_delay=1.0, max_delay=30.0):
    attempt = 0
    while True:
        try:
            return request_fn()
        except Exception as e:
            attempt += 1
            if attempt >= max_attempts:
                raise
            sleep_s = min(max_delay, base_delay * (2 ** (attempt - 1)))
            sleep_s *= (0.5 + random.random())
            logger.warning(f"Retry {attempt}/{max_attempts} in {sleep_s:.2f}s...")
            time.sleep(sleep_s)

def normalize_text(s):
    if not isinstance(s, str):
        return ""
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\wáéíóúñ ]", "", s)
    return s.strip()

def similarity(a, b):
    return SequenceMatcher(None, a, b).ratio()

def group_similar_events(events, threshold=0.95):
    if not events:
        return []
    groups = []
    for evt in events:
        text = evt.get("text", "") or evt.get("msg", "") or ""
        norm = normalize_text(text)
        matched = False
        for g in groups:
            if similarity(g["norm"], norm) >= threshold:
                g["count"] += 1
                g["timestamp_last"] = evt["timestamp"]
                matched = True
                break
        if not matched:
            groups.append({
                "sample_text": text,
                "norm": norm,
                "count": 1,
                "timestamp_first": evt["timestamp"],
                "timestamp_last": evt["timestamp"],
            })
    for g in groups:
        g.pop("norm", None)
    return groups

def fetch_events(since_dt):
    """Fetch events from MongoDB since a given datetime."""
    query = {
        "$or": [
            {"timestamp": {"$gte": since_dt}},
            {"timestamp": {"$gte": since_dt.isoformat()}},
        ]
    }
    docs = col_events.find(query).sort("timestamp", 1)
    events = []
    for doc in docs:
        ts = doc.get("timestamp")
        # Normalize timestamp to ISO string
        if isinstance(ts, datetime):
            ts_str = ts.replace(tzinfo=timezone.utc).isoformat()
        else:
            ts_str = str(ts)
        
        doc["timestamp"] = ts_str
        doc.pop("_id", None)
        events.append(doc)
    return events

def analyze_with_llm(grouped_events):
    if not grouped_events:
        return {"score": 0, "text": "Sin eventos recientes."}

    if not OPENAI_API_KEY:
        return {"score": 0, "text": "Error: OPENAI_API_KEY no configurada."}

    payload = {
        "prompt": PROMPT_ANALYSIS,
        "events": grouped_events
    }

    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": "Eres Victoria, una IA de monitoreo. Responde JSON."},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    def _req():
        return requests.post("https://api.openai.com/v1/chat/completions", headers=headers, json=body, timeout=30)

    try:
        r = with_retries(_req)
        r.raise_for_status()
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as e:
        logger.error(f"LLM Error: {e}")
        return {"score": 0, "text": f"Error analizando eventos: {str(e)}"}

# ======================
# WORKER LOGIC
# ======================

def process_block_cycle(duration_minutes, field_prefix, use_simple_key=False):
    """
    Generic logic to process a time block.
    timestamp_format: "HH" (if simple) or "HH:MM" (if detailed)
    field_prefix: "blocks" or "blocks_detailed"
    """
    now_utc = datetime.now(timezone.utc)
    today_str = now_utc.date().isoformat()
    
    total_minutes = now_utc.hour * 60 + now_utc.minute
    block_start_minute = (total_minutes // duration_minutes) * duration_minutes
    
    start_hour = block_start_minute // 60
    start_minute = block_start_minute % 60
    
    if use_simple_key:
        # Legacy 3H format: "00", "03"
        block_key = f"{start_hour:02d}"
    else:
        # Detailed format: "HH:MM"
        block_key = f"{start_hour:02d}:{start_minute:02d}"
        
    block_start_dt = now_utc.replace(hour=start_hour, minute=start_minute, second=0, microsecond=0)
    
    # Fetch
    raw_events = fetch_events(block_start_dt)
    
    if not raw_events and field_prefix == "blocks_detailed":
        # Optimization: Don't spam empty detailed blocks if not strictly needed?
        # But we might want to show "active" even if empty.
        pass

    # Group
    grouped = group_similar_events(raw_events)
    
    # Analyze
    analysis = analyze_with_llm(grouped)
    
    # Prepare Data
    block_data = {
        "text": analysis.get("text", ""),
        "score": analysis.get("score", 0),
        "events_count": len(raw_events),
        "last_updated": now_utc.isoformat(),
        "status": "active",
        "events_detail": [
            {
                "time": g.get("timestamp_last"),
                "text": g.get("sample_text"),
                "count": g.get("count", 1)
            }
            for g in grouped
        ]
    }
    
    # Update DB
    col_daily = db["victoria_daily_reports"]
    update_ops = {
        "$set": {
            f"{field_prefix}.{block_key}": block_data,
            "last_updated": now_utc.isoformat()
        }
    }
    col_daily.update_one({"date": today_str}, update_ops, upsert=True)
    logger.info(f"[{field_prefix}] Updated block {block_key} ({len(raw_events)} events)")

def run_calculation():
    logger.info("--- Starting Calculation Cycle ---")
    
    # 1. Process Standard 3H Blocks (Legacy)
    # 3 hours = 180 minutes
    process_block_cycle(180, "blocks", use_simple_key=True)
    
    # 2. Process Detailed Blocks (Configurable)
    # Only if different from 180 to avoid double work? 
    # Or just always do it if user wants the detailed view.
    if BLOCK_DURATION_MINUTES != 180:
        process_block_cycle(BLOCK_DURATION_MINUTES, "blocks_detailed", use_simple_key=False)

    # 6. Generate Daily Summary (New)
    # Fetch the full document to see all blocks
    daily_doc = col_daily.find_one({"date": today_str})
    if daily_doc and "blocks" in daily_doc:
        all_blocks = daily_doc["blocks"]
        # collect texts from all existing blocks
        daily_texts = []
        total_score = 0
        count_blocks = 0
        
        for _, b_val in all_blocks.items():
            if b_val.get("text"):
                daily_texts.append(b_val.get("text"))
            total_score += b_val.get("score", 0)
            count_blocks += 1
            
        combined_text = " ".join(daily_texts)
        if combined_text:
            # Simple aggregation or quick LLM summary of the summaries
            daily_avg_score = total_score / max(1, count_blocks)
            
            # Request a high-level summary of the day so far
            prompt_daily = f"""
            Genera un resumen ejecutivo de un solo párrafo del estado de seguridad de todo el día, 
            basado en estos reportes parciales:
            {combined_text}
            
            Retorna JSON: {{"text": "<resumen>", "score": <0.0-1.0 promedio global>}}
            Ignora el score calculado y usa tu criterio basado en los eventos.
            """
            
            try:
                # Reuse the analyze helper but with custom prompt payload?
                # Actually, let's just do a quick request here or refactor analyze_llm to accept custom prompt.
                # For speed, let's just refactor analyze_llm slightly or copy a minimal version.
                # Attempting to re-use analyze_llm won't work easily as it expects a list of events.
                # Let's do a direct call.
                
                if OPENAI_API_KEY:
                    daily_payload = {
                        "model": OPENAI_MODEL,
                        "messages": [
                            {"role": "system", "content": "Eres Victoria. Resumes seguridad diaria. JSON output."},
                            {"role": "user", "content": prompt_daily}
                        ],
                        "temperature": 0.3,
                        "response_format": {"type": "json_object"}
                    }
                    
                    def _r_daily():
                        return requests.post("https://api.openai.com/v1/chat/completions", 
                                          headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
                                          json=daily_payload, timeout=30)
                    
                    try:
                        rd = with_retries(_r_daily)
                        if rd.status_code == 200:
                            daily_res = json.loads(rd.json()["choices"][0]["message"]["content"])
                            
                            col_daily.update_one(
                                {"date": today_str}, 
                                {"$set": {
                                    "daily_summary": daily_res.get("text", "Sin resumen"),
                                    "daily_score": float(daily_res.get("score", daily_avg_score))
                                }}
                            )
                            logger.info("Updated Daily Summary.")
                    except Exception as e:
                        logger.error(f"Failed to generate daily summary: {e}")

            except Exception as e:
                logger.error(f"Daily summary logic error: {e}")

def main():
    logger.info(f"Starting Victoria Brain Worker (Model: {OPENAI_MODEL}, Interval: {CHECK_INTERVAL}s)")
    
    # Validate keys
    if not OPENAI_API_KEY:
        logger.warning("⚠️ OPENAI_API_KEY is missing! Analysis will fail.")

    while True:
        try:
            run_calculation()
        except Exception as e:
            logger.error(f"Error during calculation cycle: {e}")
            # If DB connection drops, maybe we should exit or re-init?
            # For now just sleep and retry.
        
        logger.info(f"Sleeping for {CHECK_INTERVAL} seconds...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()