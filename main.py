from fastapi import FastAPI
from pydantic import BaseModel
import os
import csv
import json
import requests
from datetime import datetime

app = FastAPI()

# ✅ Data model matches your frontend payload
class ScrapeData(BaseModel):
    # Fields the user wants to extract
    fields: list[str]
    # Full page text content
    rawContent: str


# ✅ Function to call DeepSeek via OpenRouter
def _build_messages(fields: list[str], raw_content: str) -> list[dict]:
    schema = {"rows": [{field: "string" for field in fields}]}
    system = (
        "You are an expert data extractor. Output must be STRICT JSON only. "
        "No markdown, no explanations. Use exactly the requested headers as keys. "
        "If a value is missing, use an empty string. If multiple items exist, return multiple objects in `rows`."
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
    """Call DeepSeek via OpenRouter and return (rows, raw_text)."""
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


# ✅ Save processed data to CSV
def save_rows_to_csv(headers: list[str], rows: list[dict], timestamp: str) -> str | None:
    csv_filename = "scraped_data.csv"
    file_exists = os.path.isfile(csv_filename)
    try:
        with open(csv_filename, "a", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["timestamp"] + headers
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            for row in rows:
                safe_row = {h: row.get(h, "") for h in headers}
                writer.writerow({"timestamp": timestamp, **safe_row})
        return csv_filename
    except Exception as e:
        print(f"Error saving to CSV: {e}")
        return None


# ✅ Main API endpoint
@app.post("/process")
def process_scrape_data(data: ScrapeData):
    api_key = os.getenv("OPENROUTER_KEY")
    if not api_key:
        return {"error": "OpenRouter API key not configured"}

    try:
        # Ask DeepSeek to extract strictly the requested fields
        rows, raw_text = format_data_with_deepseek(data.fields, data.rawContent, api_key)

        timestamp = datetime.now().isoformat()
        csv_path = None
        if rows:
            csv_path = save_rows_to_csv(data.fields, rows, timestamp)

        return {
            "status": "success" if rows else "no_rows",
            "fields_requested": data.fields,
            "rows": rows,
            "model_raw": raw_text,
            "csv_path": csv_path,
            "timestamp": timestamp
        }

    except Exception as e:
        return {"error": f"Processing failed: {str(e)}"}


# ✅ Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "Snaplytics Data Processor"}
