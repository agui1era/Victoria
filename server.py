import os
from flask import Flask, request
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# ===== CONFIG =====
MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB_NAME")
CACHE_COLL = "victoria_cache"
APIKEY = os.getenv("VICTORIA_APIKEY")

# ===== Mongo =====
mongo = MongoClient(MONGO_URI)
col_cache = mongo[MONGO_DB][CACHE_COLL]


# ===== Sanitizador para Alexa =====
def limpiar_para_alexa(texto):
    if not texto:
        return "Sin información disponible."
    texto = texto.replace("<", "")
    texto = texto.replace(">", "")
    texto = texto.replace("&", " y ")
    texto = texto.replace('"', "")
    texto = texto.replace("'", "")
    return texto.strip()


# ===== Seguridad =====
def check_apikey(req):
    return req.args.get("apikey") == APIKEY


# ===== Función para obtener informe desde Mongo =====
def obtener_cache(tipo):
    doc = col_cache.find_one({"tipo": tipo})
    if not doc:
        return "Aún no existe un informe de este tipo."
    return limpiar_para_alexa(doc.get("texto", "Sin información."))


# ===== ENDPOINTS =====
@app.get("/informe_actual")
def informe_actual():
    if not check_apikey(request):
        return {"error": "apikey inválida"}, 403
    return {"resultado": obtener_cache("actual")}


@app.get("/informe_tres")
def informe_tres():
    if not check_apikey(request):
        return {"error": "apikey inválida"}, 403
    return {"resultado": obtener_cache("tres")}


@app.get("/informe_dia")
def informe_dia():
    if not check_apikey(request):
        return {"error": "apikey inválida"}, 403
    return {"resultado": obtener_cache("dia")}


@app.get("/informe_ayer")     # <---- NUEVO
def informe_ayer():
    if not check_apikey(request):
        return {"error": "apikey inválida"}, 403
    return {"resultado": obtener_cache("ayer")}


# ===== MAIN =====
if __name__ == "__main__":
    print("Victoria Server (solo lectura) en puerto 8080 🦊🔥")
    app.run(host="0.0.0.0", port=8080)