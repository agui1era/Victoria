import os
import json
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient
from dotenv import load_dotenv
import hashlib

load_dotenv()

# =======================
# CONFIG
# =======================

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PROMPT_ANALYSIS = os.getenv("PROMPT_ANALYSIS")

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB_NAME")
EVENTS_COLL = os.getenv("MONGO_COLL_NAME")
CACHE_COLL = "victoria_cache"

# Modelos por tipo
MODEL_ACTUAL = os.getenv("MODEL_ACTUAL", "gpt-4o-mini")
MODEL_TRES   = os.getenv("MODEL_TRES",   "gpt-4o-mini")
MODEL_DIA    = os.getenv("MODEL_DIA",    "gpt-4o-mini")
MODEL_AYER   = os.getenv("MODEL_AYER",   "gpt-4.1")

mongo = MongoClient(MONGO_URI)
db = mongo[MONGO_DB]
col_events = db[EVENTS_COLL]
col_cache  = db[CACHE_COLL]

# =======================
# HELPERS
# =======================

def normalize_text(s):
    s = s.lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\wáéíóúñ ]", "", s)
    return s.strip()

def fingerprint(text):
    """
    Genera un fingerprint rápido usando n-grams.
    Mucho más eficiente que difflib.
    """
    text = normalize_text(text)
    if not text:
        return None

    base = text[:200]  # límite para performance
    ngrams = [base[i:i+5] for i in range(0, len(base), 5)]
    mezcla = "|".join(ngrams)
    return hashlib.md5(mezcla.encode("utf8")).hexdigest()


def group_similar(events):
    """
    Agrupador ultra-rápido.
    O(n), ideal para grandes cantidades.
    """
    grupos = []
    seen = {}

    for evt in events:
        txt = evt.get("text", "") or evt.get("msg", "")
        if not txt:
            continue

        fp = fingerprint(txt)
        if not fp:
            continue

        if fp in seen:
            seen[fp]["count"] += 1
        else:
            grupo = {
                "sample_text": txt,
                "count": 1
            }
            grupos.append(grupo)
            seen[fp] = grupo

    return grupos


def read_events(minutes):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    docs = col_events.find({"timestamp": {"$gte": cutoff.isoformat()}}).sort("timestamp", 1)

    eventos = []
    for d in docs:
        ts = d.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))

        d["timestamp"] = ts.isoformat()
        d.pop("_id", None)
        eventos.append(d)

    return eventos


def read_events_range(start, end):
    docs = col_events.find({
        "timestamp": {
            "$gte": start.isoformat(),
            "$lte": end.isoformat()
        }
    }).sort("timestamp", 1)

    eventos = []
    for d in docs:
        ts = d.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))

        d["timestamp"] = ts.isoformat()
        d.pop("_id", None)
        eventos.append(d)

    return eventos


def limpiar_para_alexa(texto):
    if not texto:
        return "Sin información."

    texto = re.sub(r"\*\*(.*?)\*\*", r"\1", texto)
    texto = re.sub(r"\*(.*?)\*", r"\1", texto)
    texto = texto.replace("<", "").replace(">", "")
    texto = texto.replace("&", " y ")
    texto = texto.replace('"', "").replace("'", "")
    texto = texto.replace("\n- ", ". ").replace("\n* ", ". ")
    texto = texto.replace("\n1. ", ". ")
    texto = re.sub(r"\n+", " ", texto)

    return texto.strip()


# =======================
# CACHE
# =======================

def leer_cache(tipo):
    return col_cache.find_one({"tipo": tipo})


def guardar_cache(tipo, texto, events_hash):
    col_cache.update_one(
        {"tipo": tipo},
        {
            "$set": {
                "tipo": tipo,
                "texto": texto,
                "events_hash": events_hash,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        },
        upsert=True
    )


# =======================
# LLM
# =======================

def analizar(eventos, modelo):
    if not eventos:
        return "No hubo eventos relevantes en este periodo."

    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": PROMPT_ANALYSIS},
            {"role": "user", "content": json.dumps(eventos, ensure_ascii=False)}
        ]
    }

    print(f"\n🔵 [{modelo}] Victoria → OpenAI")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json=payload,
            timeout=40
        )
        r.raise_for_status()

        print("\n🟣 OpenAI RAW:")
        print(r.text[:500])

        texto = r.json()["choices"][0]["message"]["content"]
        return limpiar_para_alexa(texto)

    except Exception as e:
        print("❌ ERROR OPENAI:", e)
        return "Error procesando eventos."


# =======================
# LOGICA DE RE-CÁLCULO
# =======================

def procesar_si_cambia(tipo, eventos, modelo):
    events_hash = hash(json.dumps(eventos, ensure_ascii=False))
    cache_prev = leer_cache(tipo)

    if cache_prev and cache_prev.get("events_hash") == events_hash:
        print(f"🔵 {tipo.upper()} sin cambios → skip.")
        return

    print(f"🟣 {tipo.upper()} actualizado → recalculando con {modelo}...")
    texto = analizar(eventos, modelo)
    guardar_cache(tipo, texto, events_hash)
    print(f"🟢 {tipo.upper()} OK.")


# =======================
# MAIN LOOP
# =======================

def main():
    print("🔥 Victoria PreCalculator ULTRA ONLINE (cada 5 minutos)")

    while True:
        try:
            print("\n=========================")
            print("🔄 Ejecutando ciclo ULTRA")
            print("=========================")

            # 1) Actual (5 min)
            procesar_si_cambia("actual", group_similar(read_events(5)), MODEL_ACTUAL)

            # 2) Tres horas
            procesar_si_cambia("tres", group_similar(read_events(180)), MODEL_TRES)

            # 3) Día (24h ventana móvil)
            procesar_si_cambia("dia", group_similar(read_events(1440)), MODEL_DIA)

            # 4) Ayer (solo 1 vez por día)
            hoy = datetime.now(timezone.utc).date()
            ayer = hoy - timedelta(days=1)

            cache_ayer = leer_cache("ayer")

            if not cache_ayer:
                print("🟣 AYER no existe → calcularlo ahora.")
                recalcular_ayer = True

            else:
                fecha_cache = cache_ayer["timestamp"].split("T")[0]
                recalcular_ayer = fecha_cache != str(hoy)

                if not recalcular_ayer:
                    print("🔵 AYER ya calculado hoy → skip.")

            if recalcular_ayer:
                print(f"🟣 Recalculando AYER con modelo caro {MODEL_AYER}...")

                start = datetime(ayer.year, ayer.month, ayer.day, 0, 0, 0, tzinfo=timezone.utc)
                end   = datetime(ayer.year, ayer.month, ayer.day, 23, 59, 59, tzinfo=timezone.utc)

                eventos_ayer = group_similar(read_events_range(start, end))
                hash_ayer = hash(json.dumps(eventos_ayer, ensure_ascii=False) + str(ayer))

                texto_ayer = analizar(eventos_ayer, MODEL_AYER)
                guardar_cache("ayer", texto_ayer, hash_ayer)

                print("🟢 AYER listo.")

        except Exception as e:
            print("❌ ERROR GENERAL:", e)

        print("⏳ Durmiendo 5 minutos...\n")
        time.sleep(600)


if __name__ == "__main__":
    main()
