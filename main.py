from fastapi import FastAPI
from pydantic import BaseModel
from pymongo import MongoClient
import os
import json
import requests
from datetime import datetime
import uuid
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# --- FastAPI app ---
app = FastAPI()

# --- MongoDB setup ---
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
client = MongoClient(MONGO_URI)
db = client["snaplytics_db"]
collection = db["scraped_data"]

# --- Data model ---
class ScrapeData(BaseModel):
    userId: str | None = None  # optional (will auto-generate)
    fields: list[str]
    rawContent: str


# --- DeepSeek Formatter ---
def _build_messages(fields: list[str], raw_content: str) -> list[dict]:
    schema = {"rows": [{field: "string" for field in fields}]}
    system = (
        "You are an expert data extractor. Output must be STRICT JSON only. "
        "No markdown, no explanations. Use exactly the requested headers as keys. "
        "If a value is missing, use an empty string. "
        "If multiple items exist, return multiple objects in `rows`."
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


def _parse_model_json(text: str) -> list[dict]:
    if not text:
        return []
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 1)[-1]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]
    cleaned = cleaned.strip()
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return [r for r in payload["rows"] if isinstance(r, dict)]
        if isinstance(payload, list):
            return [r for r in payload if isinstance(r, dict)]
    except Exception:
        pass
    return []


def format_data_with_deepseek(fields: list[str], raw_content: str, api_key: str):
    messages = _build_messages(fields, raw_content)
    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek/deepseek-r1",
                "messages": messages
            },
            timeout=60
        )
        if response.status_code == 200:
            result = response.json()
            raw_text = result["choices"][0]["message"]["content"]
            rows = _parse_model_json(raw_text)
            return rows, raw_text
        else:
            return [], f"Error: {response.status_code} - {response.text}"
    except Exception as e:
        return [], f"Error formatting data: {str(e)}"


# --- Main Endpoint ---
@app.post("/process")
def process_scrape_data(data: ScrapeData):
    api_key = os.getenv("OPENROUTER_KEY")
    if not api_key:
        return {"error": "OpenRouter API key not configured"}

    try:
        user_id = data.userId or str(uuid.uuid4())
        rows, raw_text = format_data_with_deepseek(data.fields, data.rawContent, api_key)
        timestamp = datetime.now().isoformat()

        # Save data to MongoDB
        record = {
            "userId": user_id,
            "fields_requested": data.fields,
            "rows": rows,
            "model_raw": raw_text,
            "timestamp": timestamp
        }
        collection.insert_one(record)

        return {
            "status": "success" if rows else "no_rows",
            "userId": user_id,
            "fields_requested": data.fields,
            "rows": rows,
            "timestamp": timestamp
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}


# --- Get user data ---
@app.get("/get_user_data/{userId}")
def get_user_data(userId: str):
    docs = list(collection.find({"userId": userId}, {"_id": 0}))
    return {
        "status": "success" if docs else "no_data",
        "userId": userId,
        "records": docs,
        "count": len(docs)
    }


# --- Health check ---
@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "Snaplytics Data Processor (MongoDB)"}
