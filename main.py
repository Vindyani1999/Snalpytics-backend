from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import os
import json
import requests
from datetime import datetime
import uuid
from dotenv import load_dotenv
from pymongo import MongoClient
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional

# Load environment variables
load_dotenv()

# --- FastAPI app ---
app = FastAPI()

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://snaplytics-frontend.vercel.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- MongoDB setup ---
MONGO_URI = os.getenv("MONGO_URI")
client = MongoClient(MONGO_URI)
db = client["snaplytics_db"]
collection = db["scraped_data"]


# --- Data model ---
class ScrapeData(BaseModel):
    fields: list[str]
    rawContent: str
    userEmail: Optional[str] = None


# JWT/token verification removed — writes use extension-supplied userEmail only.


# --- DeepSeek formatter ---
def _build_messages(fields: list[str], raw_content: str) -> list[dict]:
    schema = {"rows": [{field: "string" for field in fields}]}
    system = (
        "You are an expert data extractor. Output must be STRICT JSON only. "
        "No markdown, no explanations. Use exactly the requested headers as keys."
    )
    user_payload = {
        "headers": fields,
        "text": raw_content,
        "schema": schema,
        "output_format": {"rows": [{h: "" for h in fields}]}
    }
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user_payload)},
    ]


def _parse_model_json(text: str) -> list[dict]:
    if not text:
        return []
    cleaned = text.strip().strip("```")
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
            return payload["rows"]
        if isinstance(payload, list):
            return payload
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
                "Content-Type": "application/json",
            },
            json={"model": "deepseek/deepseek-r1", "messages": messages},
            timeout=60,
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


# --- Protected endpoint ---
@app.post("/process")
def process_scrape_data(data: ScrapeData):
    api_key = os.getenv("OPENROUTER_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenRouter API key missing")

    try:
        # Require userEmail from the extension if no token flow is used.
        if data.userEmail:
            user_email = data.userEmail
            user_id = None
        else:
            # No token flow in this local handler: reject if userEmail not provided
            raise HTTPException(status_code=401, detail="Missing userEmail in payload")

        rows, raw_text = format_data_with_deepseek(data.fields, data.rawContent, api_key)
        timestamp = datetime.now().isoformat()

        record = {
            "userId": user_id,
            "userEmail": user_email,
            "fields_requested": data.fields,
            "rows": rows,
            "model_raw": raw_text,
            "timestamp": timestamp,
        }
        collection.insert_one(record)

        return {
            "status": "success" if rows else "no_rows",
            "user": {"id": user_id, "email": user_email},
            "fields_requested": data.fields,
            "rows": rows,
            "timestamp": timestamp,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.get("/get_user_data_by_email/{email}")
def get_user_data_by_email(email: str):
    docs = list(collection.find({"userEmail": email}, {"_id": 0}))

    def _try_parse_model_raw(model_raw: str):
        if not model_raw or not isinstance(model_raw, str):
            return []
        s = model_raw.strip()
        if s.startswith("```") and s.endswith("```"):
            s = s.strip("`")
        try:
            return json.loads(s)
        except Exception:
            pass
        start = None
        end = None
        for i, ch in enumerate(s):
            if ch in ('{', '['):
                start = i
                break
        for i in range(len(s) - 1, -1, -1):
            if s[i] in ('}', ']'):
                end = i + 1
                break
        if start is not None and end is not None and end > start:
            snippet = s[start:end]
            try:
                return json.loads(snippet)
            except Exception:
                return []
        return []

    normalized = []
    for d in docs:
        rows = d.get("rows") or []
        parsed_rows = rows
        if (not rows or len(rows) == 0) and d.get("model_raw"):
            parsed = _try_parse_model_raw(d.get("model_raw"))
            if isinstance(parsed, dict) and isinstance(parsed.get("rows"), list):
                parsed_rows = parsed.get("rows")
            elif isinstance(parsed, list):
                parsed_rows = parsed
        normalized.append({
            **{k: v for k, v in d.items()},
            "parsed_rows": parsed_rows,
        })

    return {
        "status": "success" if normalized else "no_data",
        "userEmail": email,
        "records": normalized,
        "count": len(normalized),
    }


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "Snaplytics Data Processor"}
