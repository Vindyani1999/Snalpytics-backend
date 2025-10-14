from fastapi import FastAPI, Header, Body
from pydantic import BaseModel
import sys
import os

# Add the parent directory to the Python path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import users, snapshots
from ai_integration import format_data
from firebase_auth_vercel import verify_token
from datetime import datetime

app = FastAPI()

class ScrapeData(BaseModel):
    url: str
    fields: dict
    timestamp: str

# Save user OpenRouter API key
@app.post("/save_api_key")
def save_api_key(api_key: str = Body(...), authorization: str = Header(...)):
    user_id = verify_token(authorization)
    if not user_id:
        return {"error": "Invalid token"}

    users.update_one(
        {"_id": user_id},
        {"$set": {
            "openrouter_key": api_key,
            "usage": {"daily_limit": 100, "used_today": 0, "last_reset": str(datetime.today().date())}
        }},
        upsert=True
    )
    return {"status": "API key saved successfully"}

# Receive scraped data
@app.post("/scrape")
def receive_scrape(data: ScrapeData, authorization: str = Header(...)):
    user_id = verify_token(authorization)
    if not user_id:
        return {"error": "Invalid token"}

    user = users.find_one({"_id": user_id})
    if not user or "openrouter_key" not in user:
        return {"error": "OpenRouter API key not set"}

    # Reset usage if new day
    today = str(datetime.today().date())
    if user["usage"]["last_reset"] != today:
        user["usage"]["used_today"] = 0
        user["usage"]["last_reset"] = today

    # Format data with user's OpenRouter key
    formatted = format_data(data.fields, user["openrouter_key"])

    # Update usage
    users.update_one(
        {"_id": user_id},
        {"$inc": {"usage.used_today": 1}, "$set": {"usage.last_reset": today}}
    )

    # Save snapshot
    snapshots.insert_one({
        "user_id": user_id,
        "url": data.url,
        "fields": formatted,
        "timestamp": datetime.fromisoformat(data.timestamp)
    })

    return {"status": "success", "formatted": formatted, "usage": user["usage"]}

# Fetch snapshots per user + URL
@app.get("/category/{user_id}/{url}")
def get_category_data(user_id: str, url: str):
    results = list(snapshots.find({"user_id": user_id, "url": url}))
    return {"url": url, "snapshots": results}

# This is required for Vercel deployment
handler = app