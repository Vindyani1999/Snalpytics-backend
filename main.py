from fastapi import FastAPI, Request, HTTPException, Depends
from pydantic import BaseModel
import os
import json
import requests
from datetime import datetime
import jwt
import uuid
from dotenv import load_dotenv
from pymongo import MongoClient
from fastapi.middleware.cors import CORSMiddleware

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

JWT_SECRET = os.getenv("JWT_SECRET")  # must match Node.js backend secret

# --- Data model ---
class ScrapeData(BaseModel):
    fields: list[str]
    rawContent: str


# --- Verify JWT token ---
def verify_token(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid token")
    token = auth_header.split(" ")[1]
    try:
        decoded = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return decoded
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


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
def process_scrape_data(data: ScrapeData, user=Depends(verify_token)):
    api_key = os.getenv("OPENROUTER_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OpenRouter API key missing")

    try:
        user_id = user["id"]
        user_email = user["email"]

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


@app.get("/get_user_data")
def get_user_data(user=Depends(verify_token)):
    user_id = user["id"]
    docs = list(collection.find({"userId": user_id}, {"_id": 0}))
    return {
        "status": "success" if docs else "no_data",
        "user": {"id": user_id, "email": user["email"]},
        "records": docs,
        "count": len(docs),
    }


@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "Snaplytics Data Processor"}
