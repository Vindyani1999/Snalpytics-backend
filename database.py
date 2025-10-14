from pymongo import MongoClient
import os
from dotenv import load_dotenv

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI"))
if client:
    print("Connected to MongoDB")
else:
    print("Failed to connect to MongoDB")
db = client.snaplytics_db
users = db.users
snapshots = db.snapshots
