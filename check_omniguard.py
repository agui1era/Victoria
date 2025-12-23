import os
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI")

# Force check omniguard
MONGO_DB = "omniguard"
EVENTS_COLL = "events"

print(f"Checking DB: {MONGO_DB}")

client = MongoClient(MONGO_URI)
db = client[MONGO_DB]
col = db[EVENTS_COLL]

count = col.count_documents({})
print(f"Total documents in {MONGO_DB}.{EVENTS_COLL}: {count}")
