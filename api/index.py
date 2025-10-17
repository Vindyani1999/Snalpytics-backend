from fastapi import FastAPI
from pydantic import BaseModel
import os
import json
import requests
from datetime import datetime
import uuid
from dotenv import load_dotenv

app = FastAPI()

# Load .env for local development (Vercel uses dashboard env vars in production)
load_dotenv()

# --- MongoDB setup (optional, lazy import) ---
MONGO_URI = os.getenv("MONGO_URI")
client = None
collection = None
if MONGO_URI:
    try:
        # import lazily so deployments that don't include pymongo won't fail at import time
        from pymongo import MongoClient as _MongoClient
        # short timeout to avoid long cold-start delays in serverless
        client = _MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        db = client["snaplytics_db"]
        collection = db["scraped_data"]
    except Exception as e:
        # Don't crash the process; leave collection as None and log the error
        print(f"Warning: could not connect to MongoDB or import pymongo: {e}")

# --- Auth Verify URL (Node.js backend endpoint) ---
AUTH_VERIFY_URL = os.getenv("AUTH_VERIFY_URL")

class ScrapeData(BaseModel):
    fields: list[str]
    rawContent: str


def _build_messages(fields, raw_content):
    schema = {"rows": [{field: "string" for field in fields}]}
    system = (
        "You are an expert data extractor. Output must be STRICT JSON only. "
        "No markdown, no explanations. Use exactly the requested headers as keys. "
        "If missing, leave blank. Return multiple `rows` if multiple items exist."
    )
    user_payload = {
        "headers": fields,
        "text": raw_content,
        "schema": schema,
        "output_format": {"rows": [{h: "" for h in fields}]}
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload)}
    ]


def _parse_model_json(text):
    try:
        cleaned = text.strip("`").strip()
        payload = json.loads(cleaned)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return payload["rows"]
        if isinstance(payload, list):
            return payload
    except Exception:
        return []
    return []


def format_data_with_deepseek(fields, raw_content, api_key):
    messages = _build_messages(fields, raw_content)
    try:
        r = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={"model": "deepseek/deepseek-r1", "messages": messages},
            timeout=60
        )
        if r.status_code == 200:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            rows = _parse_model_json(content)
            return rows, content
        return [], f"Error: {r.status_code} - {r.text}"
    except Exception as e:
        return [], f"Error formatting data: {str(e)}"

# --- MAIN ENDPOINT ---
@app.post("/process")
def process_scrape_data(
    data: ScrapeData,
    authorization: Optional[str] = Header(None)
):
    api_key = os.getenv("OPENROUTER_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="Missing OpenRouter API key")

    # 🔐 Verify Google login token via Node backend
    user_info = None
    if authorization:
        try:
            res = requests.get(
                AUTH_VERIFY_URL,
                headers={"Authorization": authorization},
                timeout=10
            )
            if res.status_code == 200:
                payload = res.json()
                if payload.get("valid"):
                    user_info = payload.get("user")
        except Exception as e:
            print("Token verification failed:", e)

    if not user_info:
        raise HTTPException(status_code=401, detail="Unauthorized or invalid token")

    # ✅ Extract user ID or email
    user_id = user_info.get("email") or user_info.get("id") or str(uuid.uuid4())

    rows, raw_text = format_data_with_deepseek(data.fields, data.rawContent, api_key)
    timestamp = datetime.now().isoformat()

    doc = {
        "userId": user_id,
        "fields_requested": data.fields,
        "rows": rows,
        "model_raw": raw_text,
        "timestamp": timestamp
    }

    db_status = "skipped"
    if collection is not None:
        try:
            collection.insert_one(doc)
            db_status = "saved"
        except Exception as e:
            db_status = f"error: {str(e)}"

    return {
        "status": "success",
        "userId": user_id,
        "rows": rows,
        "timestamp": timestamp,
        "db_status": db_status
    }

# @app.post("/process")
# def process_scrape_data(data: ScrapeData):
#     api_key = os.getenv("OPENROUTER_KEY")
#     if not api_key:
#         return {"status": "error", "message": "Missing OpenRouter API key"}

#     user_id = data.userId or str(uuid.uuid4())
#     rows, raw_text = format_data_with_deepseek(data.fields, data.rawContent, api_key)
#     timestamp = datetime.now().isoformat()

#     doc = {
#         "userId": user_id,
#         "fields_requested": data.fields,
#         "rows": rows,
#         "model_raw": raw_text,
#         "timestamp": timestamp
#     }

#     db_status = "skipped"
#     if collection is not None:
#         try:
#             collection.insert_one(doc)
#             db_status = "saved"
#         except Exception as e:
#             # don't raise — return info so caller knows DB write failed
#             db_status = f"error: {str(e)}"

#     return {
#         "status": "success",
#         "userId": user_id,
#         "rows": rows,
#         "timestamp": timestamp,
#         "db_status": db_status
#     }

# --- FETCH USER DATA ---
@app.get("/get_user_data/{userId}")
def get_user_data(userId: str):
    if collection is None:
        return {"status": "error", "message": "DB not configured"}
    docs = list(collection.find({"userId": userId}, {"_id": 0}))
    return {
        "status": "success" if docs else "no_data",
        "userId": userId,
        "records": docs,
        "count": len(docs)
    }

# --- Health Check ---
@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "Snaplytics (MongoDB Ready)"}


# Required for Vercel
handler = app
