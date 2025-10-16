from fastapi import FastAPI
from pydantic import BaseModel
from pymongo import MongoClient
import os
import json
import requests
from datetime import datetime
import uuid
from dotenv import load_dotenv

app = FastAPI()

# Load .env for local development (Vercel uses dashboard env vars in production)
load_dotenv()

# --- MongoDB setup ---
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client["snaplytics_db"]
collection = db["scraped_data"]

class ScrapeData(BaseModel):
    userId: str | None = None
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


@app.post("/process")
def process_scrape_data(data: ScrapeData):
    api_key = os.getenv("OPENROUTER_KEY")
    if not api_key:
        return {"error": "Missing OpenRouter API key"}

    user_id = data.userId or str(uuid.uuid4())
    rows, raw_text = format_data_with_deepseek(data.fields, data.rawContent, api_key)
    timestamp = datetime.now().isoformat()

    doc = {
        "userId": user_id,
        "fields_requested": data.fields,
        "rows": rows,
        "model_raw": raw_text,
        "timestamp": timestamp
    }
    collection.insert_one(doc)

    return {
        "status": "success",
        "userId": user_id,
        "rows": rows,
        "timestamp": timestamp
    }


@app.get("/get_user_data/{userId}")
def get_user_data(userId: str):
    docs = list(collection.find({"userId": userId}, {"_id": 0}))
    return {
        "status": "success" if docs else "no_data",
        "userId": userId,
        "records": docs,
        "count": len(docs)
    }


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "Snaplytics (MongoDB Ready)"}


# Required for Vercel
handler = app
