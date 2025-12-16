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
MONGO_DB_EVENTS = os.getenv("MONGO_DB_EVENTS", "omniguard")
MONGO_DB_CACHE = os.getenv("MONGO_DB_CACHE", "victoria")
EVENTS_COLL = os.getenv("MONGO_COLL_NAME", "events")
CACHE_COLL = "victoria_cache"


# Modelos por tipo
MODEL_ACTUAL = os.getenv("MODEL_ACTUAL", "gpt-4o-mini")
MODEL_TRES   = os.getenv("MODEL_TRES",   "gpt-4o-mini")
MODEL_DIA    = os.getenv("MODEL_DIA",    "gpt-4o-mini")


mongo = MongoClient(MONGO_URI)
db_events = mongo[MONGO_DB_EVENTS]
db_cache  = mongo[MONGO_DB_CACHE]

col_events = db_events[EVENTS_COLL]
col_cache  = db_cache[CACHE_COLL]


# =======================
# HELPERS
# =======================

def log(msg):
    now = datetime.now()
    ts = now.strftime("[%H:%M:%S]")
    print(f"{ts} {msg}")

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
        txt = evt.get("text", "") or evt.get("msg", "") or evt.get("description", "")
        if not txt:
            continue
        
        # Truncar para ahorrar tokens y memoria
        txt = txt[:500]

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


def read_last_event():
    """
    Obtiene el último registro crudo de la colección general.
    """
    doc = col_events.find().sort("timestamp", -1).limit(1)
    ultimo = next(doc, None)

    if not ultimo:
        return None

    ts = ultimo.get("timestamp")
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))

    if isinstance(ts, datetime):
        ultimo["timestamp"] = ts.isoformat()

    ultimo.pop("_id", None)
    return ultimo


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
    
    # Ordenar por importancia (count) y limitar a top 100 para no explotar el context window
    # Asumimos que 'eventos' es una lista de grupos (dicts con 'count').
    if len(eventos) > 0 and "count" in eventos[0]:
        eventos.sort(key=lambda x: x["count"], reverse=True)
        eventos = eventos[:100]
    elif len(eventos) > 100:
        # Fallback si no son grupos agrupados, crude slice
        eventos = eventos[:100]

    payload = {
        "model": modelo,
        "messages": [
            {"role": "system", "content": PROMPT_ANALYSIS},
            {"role": "user", "content": json.dumps(eventos, ensure_ascii=False)}
        ]
    }

    print(f"\n🔵 [{modelo}] Victoria → OpenAI (Payload size: {len(json.dumps(eventos))})")
    # print(json.dumps(payload, indent=2, ensure_ascii=False))  # Too verbose

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
            json=payload,
            timeout=40
        )
        r.raise_for_status()

        # print("\n🟣 OpenAI RAW:")
        # print(r.text[:500])

        texto = r.json()["choices"][0]["message"]["content"]
        return limpiar_para_alexa(texto)

    except Exception as e:
        log(f"❌ ERROR OPENAI: {e}")
        return "Error procesando eventos."


# =======================
# LOGICA DE RE-CÁLCULO
# =======================

def procesar_si_cambia(tipo, eventos, modelo):
    # Usar sort_keys=True para garantizar hash determinista
    events_hash = hash(json.dumps(eventos, ensure_ascii=False, sort_keys=True))
    cache_prev = leer_cache(tipo)

    log(f" {tipo.upper()} actualizado → recalculando con {modelo}...")
    texto = analizar(eventos, modelo)
    
    log(f"📝 Resultado {tipo.upper()}: {texto}")
    
    guardar_cache(tipo, texto, events_hash)

    log(f"🟢 {tipo.upper()} OK.")


def procesar_actual_desde_general():
    """
    El informe ACTUAL ahora toma el último registro crudo de la colección
    general, sin recalcular categorías ni resúmenes.
    """
    ultimo = read_last_event()

    if not ultimo:
        log("🔴 ACTUAL sin eventos en la colección general → Guardando estado vacío.")
        texto = "No hay eventos registrados aún."
        events_hash = "no_events"

        cache_prev = leer_cache("actual")

        guardar_cache("actual", texto, events_hash)

        log("🟢 ACTUAL (vacío) OK.")
        return

    texto = ultimo.get("text") or ultimo.get("msg") or ultimo.get("mensaje") or ultimo.get("description")
    if not texto:
        texto = json.dumps(ultimo, ensure_ascii=False)

    events_hash = hash(json.dumps(ultimo, ensure_ascii=False, default=str, sort_keys=True))
    cache_prev = leer_cache("actual")

    log("🟣 ACTUAL se toma del último registro en la colección general.")
    texto_limpio = limpiar_para_alexa(texto)
    
    log(f"📝 Resultado ACTUAL: {texto_limpio}")

    guardar_cache("actual", texto_limpio, events_hash)

    log("🟢 ACTUAL OK.")


def read_last_n_events(n):
    # Obtener los últimos N eventos (orden descendente primero)
    docs = col_events.find().sort("timestamp", -1).limit(n)

    eventos = []
    for d in docs:
        ts = d.get("timestamp")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))

        d["timestamp"] = ts.isoformat()
        d.pop("_id", None)
        eventos.append(d)

    # Revertir a orden cronológico para el análisis
    # (aunque group_similar no le importa, es mejor ser consistentes)
    eventos.reverse()
    return eventos


# =======================
# MAIN LOOP
# =======================

def main():
    log("🔥 Victoria PreCalculator ULTRA ONLINE (cada 5 minutos)")

    while True:
        try:
            print("\n=========================")
            log("🔄 Ejecutando ciclo ULTRA")
            print("=========================")

            # 1) Actual (5 min)
            procesar_actual_desde_general()

            # 2) Tres horas -> Ahora "Short Term" (últimos 200 eventos)
            ev_tres = read_last_n_events(200)
            log(f"🔎 TRES (Last 200): Encontrados {len(ev_tres)} eventos.")
            procesar_si_cambia("tres", group_similar(ev_tres), MODEL_TRES)

            # 3) Día -> Ahora "Long Term" (últimos 1000 eventos)
            ev_dia = read_last_n_events(1000)
            log(f"🔎 DIA (Last 1000): Encontrados {len(ev_dia)} eventos.")
            procesar_si_cambia("dia", group_similar(ev_dia), MODEL_DIA)

            # 4) Ayer -> DISABLED per user request
            # (Logic removed)

        except Exception as e:
            log(f"❌ ERROR GENERAL: {e}")

        log("⏳ Durmiendo 5 minutos...\n")
        time.sleep(600)


if __name__ == "__main__":
    main()
