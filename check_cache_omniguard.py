import os
from pymongo import MongoClient
from dotenv import load_dotenv
from datetime import datetime
import sys

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
# Explicitly check omniguard as configured
MONGO_DB = "omniguard"
CACHE_COLL = "victoria_cache"

print(f"Checking DB: {MONGO_DB}, Coll: {CACHE_COLL}")

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
col = db[CACHE_COLL]

count = col.count_documents({})
print(f"Total entries: {count}")

for doc in col.find():
    ts = doc.get("timestamp")
    text = doc.get("texto")
    tipo = doc.get("tipo")
    print(f"[{tipo.upper()}] TS: {ts}")
    print(f"   Text: {text[:100]}...") # Truncate for brevity
    print("-" * 20)
