from fastapi import FastAPI
from pydantic import BaseModel
import os
import csv
import requests
from datetime import datetime

# Create FastAPI app
app = FastAPI()

# --- Data Model ---
class ScrapeData(BaseModel):
    fields: list[str]       # Example: ["price", "name"]
    rawContent: str         # The full grabbed webpage text


# --- DeepSeek Formatter ---
def format_data_with_deepseek(data, api_key):
    """Format scraped data using DeepSeek model via OpenRouter API"""
    prompt = (
        "You are an intelligent data extractor.\n"
        "Given raw webpage text and a list of requested fields, "
        "extract structured information in clean JSON format.\n\n"
        f"### Requested Fields:\n{data['fields']}\n\n"
        f"### Raw Content:\n{data['rawContent']}\n\n"
        "Return only the relevant JSON output."
    )

    try:
        response = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": "deepseek/deepseek-r1",
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60
        )

        if response.status_code == 200:
            result = response.json()
            return result["choices"][0]["message"]["content"]
        else:
            return f"Error: {response.status_code} - {response.text}"

    except Exception as e:
        return f"Error formatting data: {str(e)}"


# --- Save data locally (Vercel file system is ephemeral, but OK for logs) ---
def save_to_csv(original_data, formatted_data, timestamp):
    csv_filename = "/tmp/scraped_data.csv"  # Vercel allows writing only to /tmp
    file_exists = os.path.isfile(csv_filename)

    try:
        with open(csv_filename, "a", newline="", encoding="utf-8") as csvfile:
            fieldnames = ["timestamp", "original_data", "formatted_data"]
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames)

            if not file_exists:
                writer.writeheader()

            writer.writerow({
                "timestamp": timestamp,
                "original_data": str(original_data),
                "formatted_data": formatted_data
            })
        return True
    except Exception as e:
        print(f"Error saving to CSV: {e}")
        return False


# --- Main Endpoint ---
@app.post("/process")
def process_scrape_data(data: ScrapeData):
    api_key = os.getenv("OPENROUTER_KEY")
    if not api_key:
        return {"error": "OpenRouter API key not configured"}

    try:
        # Combine data for DeepSeek
        combined_data = {"fields": data.fields, "rawContent": data.rawContent}

        # Format via DeepSeek
        formatted_data = format_data_with_deepseek(combined_data, api_key)

        # Save result (optional)
        timestamp = datetime.now().isoformat()
        csv_saved = save_to_csv(combined_data, formatted_data, timestamp)

        return {
            "status": "success",
            "fields_requested": data.fields,
            "formatted_data": formatted_data,
            "csv_saved": csv_saved,
            "timestamp": timestamp
        }

    except Exception as e:
        return {"error": f"Processing failed: {str(e)}"}


# --- Health check ---
@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "Snaplytics Data Processor"}


# --- Required for Vercel ---
# Expose handler for Vercel Python runtime
handler = app
