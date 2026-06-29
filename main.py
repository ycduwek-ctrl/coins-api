from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import gspread
from google.oauth2.service_account import Credentials
import cloudinary
import cloudinary.uploader
from PIL import Image
import io
import os
import json
import re
import base64
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_URL = "https://docs.google.com/spreadsheets/d/1Hzn0CQCoZrMPKt-Hn6NfkXl6WhJ5tZC_-YILuFsiak0"

cloudinary.config(
    cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key=os.environ["CLOUDINARY_API_KEY"],
    api_secret=os.environ["CLOUDINARY_API_SECRET"]
)

COLS = ["id", "name", "price", "country", "material", "year", "images", "comments"]

def get_sheet():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_url(SHEET_URL).worksheet("coins")

@app.get("/coins")
def get_coins():
    ws = get_sheet()
    return ws.get_all_records()

@app.post("/coins")
def add_coin(data: dict):
    ws = get_sheet()
    rows = ws.get_all_records()
    if not rows:
        ws.append_row(COLS)
    ws.append_row([data.get(c, "") for c in COLS])
    return {"ok": True}

@app.put("/coins/{coin_id}")
def update_coin(coin_id: str, data: dict):
    ws = get_sheet()
    records = ws.get_all_records()
    for i, row in enumerate(records):
        if str(row["id"]) == coin_id:
            for j, col in enumerate(COLS):
                if col in data:
                    ws.update_cell(i + 2, j + 1, data[col])
            return {"ok": True}
    return {"ok": False}

@app.delete("/coins/{coin_id}")
def delete_coin(coin_id: str):
    ws = get_sheet()
    records = ws.get_all_records()
    for i, row in enumerate(records):
        if str(row["id"]) == coin_id:
            ws.delete_rows(i + 2)
            return {"ok": True}
    return {"ok": False}

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    data = await file.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    w, h = img.size
    s = min(w, h)
    img = img.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
    img = img.resize((800, 800), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=85)
    res = cloudinary.uploader.upload(buf.getvalue(), format="webp")
    return {"url": res["secure_url"]}

@app.post("/identify")
async def identify(front: UploadFile = File(...), back: UploadFile = File(None)):
    def compress(f):
        img = Image.open(io.BytesIO(f)).convert("RGB")
        img.thumbnail((600, 600), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return base64.b64encode(buf.getvalue()).decode()

    front_b64 = compress(await front.read())
    content = [
        {"type": "text", "text": """You are a coin expert. Identify the coin and return JSON only:
{"name":"coin name","country":"country in Hebrew","year":"year","material":"כסף/זהב/נחושת/ניקל/ברונזה/אלומיניום/מתכת מעורבת","price":"estimated ILS value as number only"}"""},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{front_b64}"}}
    ]
    if back:
        back_b64 = compress(await back.read())
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{back_b64}"}})

    res = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {os.environ['OPENROUTER_KEY']}",
            "Content-Type": "application/json"
        },
        json={"model": "openrouter/auto", "messages": [{"role": "user", "content": content}]},
        timeout=30
    )
    result = res.json()
    if "error" in result:
        return {"error": result["error"]["message"]}
    text = result["choices"][0]["message"]["content"].strip()
    text = re.sub(r'```json|```', '', text).strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)
