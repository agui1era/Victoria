import os
from pymongo import MongoClient
from dotenv import load_dotenv
import sys

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB = os.getenv("MONGO_DB_NAME")
EVENTS_COLL = os.getenv("MONGO_COLL_NAME")

print(f"URI: {MONGO_URI}")
print(f"DB: {MONGO_DB}")
print(f"COLL: {EVENTS_COLL}")

if not MONGO_URI or not MONGO_DB or not EVENTS_COLL:
    print("❌ Missing env vars")
    sys.exit(1)

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
col = db[EVENTS_COLL]

count = col.count_documents({})
print(f"Total documents in {EVENTS_COLL}: {count}")

if count > 0:
    print("Last 3 events (sorted by timestamp desc):")
    for doc in col.find().sort("timestamp", -1).limit(3):
        print(f" - ID: {doc.get('_id')}, TS: {doc.get('timestamp')}, Text: {doc.get('text') or doc.get('msg')}")
else:
    print("⚠️ Collection is empty.")
