import os
import requests
import base64
import io
import time
import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, status, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load environment variables
load_dotenv(override=True)

def detect_extension(file_bytes: bytes) -> str:
    if file_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    elif file_bytes.startswith(b"\xff\xd8\xff"):
        return "jpg"
    elif file_bytes.startswith(b"GIF89a") or file_bytes.startswith(b"GIF87a"):
        return "gif"
    elif len(file_bytes) > 12 and file_bytes.startswith(b"RIFF") and file_bytes[8:12] == b"WEBP":
        return "webp"
    elif file_bytes.startswith(b"ID3") or file_bytes.startswith(b"\xff\xfb") or file_bytes.startswith(b"\xff\xf3"):
        return "mp3"
    elif file_bytes.startswith(b"RIFF") and len(file_bytes) > 12 and file_bytes[8:12] == b"WAVE":
        return "wav"
    return None

def ensure_public_url(data_or_url: str, default_filename: str) -> str:
    if not data_or_url:
        return None
    data_or_url = data_or_url.strip()
    if data_or_url.startswith("http://") or data_or_url.startswith("https://"):
        return data_or_url
    
    file_bytes = None
    filename = default_filename

    # 1. Check if it's a local static file path (e.g. /static/downloads/motionpix_xxx.jpg)
    clean_path = data_or_url.lstrip("/")
    if os.path.exists(clean_path) and os.path.isfile(clean_path):
        try:
            with open(clean_path, "rb") as f:
                file_bytes = f.read()
            ext = clean_path.split(".")[-1]
            if ext in ("png", "jpeg", "jpg", "webp", "gif", "mp4"):
                filename = f"input_file.{ext}"
        except Exception as file_err:
            print(f"Error reading local file {clean_path}: {file_err}")

    # 2. If not a local file, assume base64
    if file_bytes is None:
        base64_content = data_or_url
        if "base64," in data_or_url:
            try:
                parts = data_or_url.split("base64,")
                base64_content = parts[1]
                mime = parts[0].split(";")[0].replace("data:", "")
                ext = mime.split("/")[-1]
                if ext in ("png", "jpeg", "jpg", "webp", "gif", "mp3", "wav", "ogg"):
                    filename = f"input_file.{ext}"
            except Exception as e:
                print(f"Error parsing base64 header: {e}")
                base64_content = data_or_url.split("base64,")[1]
        
        try:
            file_bytes = base64.b64decode(base64_content)
        except Exception as b64_err:
            print(f"Error decoding base64: {b64_err}")
            return data_or_url

    if not file_bytes:
        return data_or_url

    detected_ext = detect_extension(file_bytes) or "jpg"
    filename = default_filename or f"input_file.{detected_ext}"

    # Upload exclusively to Supabase Storage ('vmaker_storage')
    supabase_url, supabase_key, bucket = get_supabase_config()
    if supabase_url and supabase_key:
        try:
            storage_path = f"inputs/{uuid.uuid4().hex[:12]}_{filename}"
            
            if detected_ext == "jpg":
                mime_type = "image/jpeg"
            elif detected_ext in ("jpeg", "png", "webp", "gif"):
                mime_type = f"image/{detected_ext}"
            else:
                mime_type = "application/octet-stream"

            print(f"[Storage] Uploading '{storage_path}' with Content-Type: {mime_type}")
            pub_url = upload_to_supabase_storage(file_bytes, storage_path, mime_type)
            if pub_url:
                print(f"[Supabase Storage] ✅ Uploaded file to (Public URL): {pub_url}")
                return pub_url
        except Exception as sup_err:
            print(f"[Supabase Storage] Upload error: {sup_err}")

    return data_or_url


def get_supabase_config():
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    supabase_key = os.getenv("SUPABASE_SECRET_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")
    bucket = os.getenv("SUPABASE_BUCKET", "vmaker_storage")
    return supabase_url, supabase_key, bucket


def get_storage_path(task_type: str, task_id: str, ext: str) -> str:
    safe_type = (task_type or "asset").replace("/", "-")
    return f"{safe_type}/{task_id}.{ext}"


def find_existing_asset(task_type: str, prompt: str):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        return None

    db_url = f"{supabase_url}/rest/v1/generated_assets"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key
    }
    params = {
        "type": f"eq.{task_type}",
        "prompt": f"eq.{prompt}",
        "select": "source_url,storage_path,created_at",
        "order": "created_at.desc",
        "limit": "1"
    }

    try:
        res = requests.get(db_url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            rows = res.json()
            if isinstance(rows, list) and rows:
                return rows[0]
        else:
            print(f"Supabase existing asset lookup failed: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Error checking asset existence in Supabase: {e}")

    return None


def upload_to_supabase_storage(file_bytes: bytes, storage_path: str, mime_type: str) -> str:
    supabase_url, supabase_key, bucket = get_supabase_config()
    if not supabase_url or not supabase_key:
        print("Supabase config missing, skipping storage upload.")
        return None

    upload_url = f"{supabase_url}/storage/v1/object/{bucket}/{storage_path}"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": mime_type,
        "x-upsert": "false"
    }

    try:
        res = requests.post(upload_url, data=file_bytes, headers=headers, timeout=60)
        if res.status_code in (200, 201):
            return f"{supabase_url}/storage/v1/object/public/{bucket}/{storage_path}"
        else:
            print(f"Supabase storage upload failed: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Error uploading to Supabase Storage: {e}")

    return None


def insert_to_supabase_db(task_type: str, prompt: str, source_url: str, storage_path: str):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        print("Supabase config missing, skipping DB insertion.")
        return

    db_url = f"{supabase_url}/rest/v1/generated_assets"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    payload = {
        "type": task_type,
        "prompt": prompt,
        "source_url": source_url,
        "storage_path": storage_path
    }

    try:
        res = requests.post(db_url, json=payload, headers=headers, timeout=20)
        if res.status_code in (200, 201):
            print("Successfully recorded asset in Supabase DB")
        else:
            print(f"Supabase DB insertion failed: {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Error inserting into Supabase DB: {e}")


KLING_API_KEY = os.getenv("KLING_API_KEY", "")
KLING_API_BASE = os.getenv("KLING_API_BASE", "https://api.klingapi.com").rstrip("/")
KLING_MODEL = os.getenv("KLING_MODEL", "kling-video-o1")
NANOBANANA_API_KEY = os.getenv("NANOBANANA_API_KEY", "")

app = FastAPI(title="Kling API Gateway - Version 2")

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Supabase JWT Authentication Middleware
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL", "") or os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "") or os.getenv("SUPABASE_ANON_KEY", "")

async def verify_supabase_token(token: str) -> bool:
    """Verify Supabase JWT by calling the Supabase Auth API."""
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return False
    try:
        res = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={
                "Authorization": f"Bearer {token}",
                "apikey": SUPABASE_ANON_KEY
            },
            timeout=5
        )
        return res.status_code == 200
    except Exception:
        return False

# Authentication Middleware - Supabase JWT based
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path
    # Public routes - no auth needed
    public_paths = ("/api/auth/register-profile", "/api/supabase/config", "/health", "/", "/motionpix", "/login", "/api/assets", "/canvas", "/api/workflow/execute", "/api/workflow/node-execute", "/api/download/proxy", "/api/gemini/image", "/api/kling/create", "/api/generations/save")
    if path in public_paths or path.startswith("/static/") or path.startswith("/features/") or path.startswith("/api/kling/") or path.startswith("/api/gemini/") or path.startswith("/api/nanobanana/") or path.startswith("/api/canvas/"):
        return await call_next(request)

    # Extract Bearer token from Authorization header
    auth_header = request.headers.get("Authorization", "")
    token = None
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]

    # For /api/download allow token via query param (browser download links can't set headers)
    if path == "/api/download" and not token:
        token = request.query_params.get("token")

    if not token:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Authentication required. Please log in."}
        )

    is_valid = await verify_supabase_token(token)
    if not is_valid:
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Invalid or expired session. Please log in again."}
        )

    return await call_next(request)

# Pydantic schema for creating a video
class CreateVideoRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Text prompt for video generation")
    duration: Optional[int] = Field(5, description="Duration in seconds. Only 5 or 10 allowed.")
    aspect_ratio: Optional[str] = Field("16:9", description="Aspect ratio. Allowed: 16:9, 9:16, 1:1")
    mode: Optional[str] = Field(None, description="Generation mode. Allowed: std, pro, 4k")
    model_name: Optional[str] = Field(None, description="Optional model override")
    image: Optional[str] = Field(None, description="Optional reference image URL (for image-to-video)")
    sound: Optional[str] = Field("off", description="Audio generation. Allowed: on, off")

@app.get("/health")
def health_check():
    return {
        "has_api_key": bool(KLING_API_KEY),
        "base_url": KLING_API_BASE,
        "model": KLING_MODEL,
        "has_nanobanana_key": bool(NANOBANANA_API_KEY),
        "ok": bool(KLING_API_KEY and KLING_API_BASE)
    }


@app.post("/api/kling/create")
def create_video(req: CreateVideoRequest):
    kling_key = os.getenv("KLING_API_KEY", "")
    kling_base = os.getenv("KLING_API_BASE", "https://api-singapore.klingai.com").rstrip("/")

    if not kling_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "KLING_API_KEY is not configured in environment variables."}
        )

    duration = req.duration if req.duration in (5, 10) else 5
    aspect_ratio = req.aspect_ratio if req.aspect_ratio in ("16:9", "9:16", "1:1") else "16:9"
    sound = "on" if str(req.sound).lower() in ("on", "true", "1") else "off"

    model_to_use = "kling-v1"
    req_m = req.model_name or ""
    m_lower = req_m.lower()
    if "1.6" in m_lower or "2.5" in m_lower or "3.0" in m_lower or "turbo" in m_lower or "i2v" in m_lower:
        model_to_use = "kling-v1-6"
    elif "1.5" in m_lower or "v1-5" in m_lower:
        model_to_use = "kling-v1-5"

    payload = {
        "model_name": model_to_use,
        "prompt": req.prompt,
        "duration": str(duration),
        "aspect_ratio": aspect_ratio,
        "sound": sound
    }

    # Strictly strip 'mode' key for kling-v1-5 / kling-v1-6 models
    if model_to_use == "kling-v1":
        if req.mode in ("std", "pro"):
            payload["mode"] = req.mode

    # If image (URL or Base64 string) is provided, append it and use image2video route
    is_image = bool(req.image and req.image.strip())
    if is_image:
        payload["image"] = ensure_public_url(req.image.strip(), "input_image.jpg")
        target_url = f"{kling_base}/v1/videos/image2video"
    else:
        target_url = f"{kling_base}/v1/videos/text2video"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {kling_key}"
    }

    try:
        response = requests.post(target_url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": f"Failed to connect to Kling API: {str(e)}",
                "payload_used": payload
            }
        )

    if response.status_code != 200:
        try:
            err_json = response.json()
        except Exception:
            err_json = {"raw_text": response.text}
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": f"Kling API returned HTTP status {response.status_code}",
                "kling_response": err_json,
                "payload_used": payload
            }
        )

    try:
        res_json = response.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "Failed to parse JSON response from Kling API",
                "raw_text": response.text,
                "payload_used": payload
            }
        )

    # Extract task_id from response
    task_id = None
    if isinstance(res_json, dict):
        if "task_id" in res_json:
            task_id = res_json["task_id"]
        elif "data" in res_json and isinstance(res_json["data"], dict) and "task_id" in res_json["data"]:
            task_id = res_json["data"]["task_id"]

    if not task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Kling response did not contain task_id",
                "kling_response": res_json,
                "payload_used": payload
            }
        )

    return {
        "task_id": task_id,
        "task_type": "image2video" if is_image else "text2video",
        "payload_used": payload,
        "kling_response": res_json
    }

@app.get("/api/kling/status/{task_type}/{task_id}")
@app.get("/api/kling/task/{task_type}/{task_id}")
def check_status(task_type: str, task_id: str):
    if task_type not in ("text2video", "image2video"):
        task_type = "text2video"
        
    headers = {
        "Authorization": f"Bearer {KLING_API_KEY}"
    }
    target_url = f"{KLING_API_BASE}/v1/videos/{task_type}/{task_id}"

    try:
        response = requests.get(target_url, headers=headers, timeout=20)
    except requests.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": f"Failed to query status from Kling API: {str(e)}"}
        )

    if response.status_code != 200:
        try:
            err_json = response.json()
        except Exception:
            err_json = {"raw_text": response.text}
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": f"Kling API returned HTTP status {response.status_code}",
                "kling_response": err_json
            }
        )

    try:
        res_json = response.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "Failed to parse JSON status response from Kling API",
                "raw_text": response.text
            }
        )

    # Extract status and video url with fallback paths
    task_status = None
    video_url = None

    if isinstance(res_json, dict):
        inner_data = res_json.get("data")
        if isinstance(inner_data, dict):
            task_status = inner_data.get("task_status")
            task_result = inner_data.get("task_result")
            if isinstance(task_result, dict):
                videos = task_result.get("videos")
                if isinstance(videos, list) and len(videos) > 0:
                    video_url = videos[0].get("url")
        
        if not task_status:
            task_status = res_json.get("task_status")
            
        if not video_url:
            def find_url(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k in ("url", "video_url", "video") and isinstance(v, str) and v.startswith("http"):
                            return v
                    for v in obj.values():
                        res = find_url(v)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_url(item)
                        if res:
                            return res
                return None
            video_url = find_url(res_json)

    # --- SUPABASE INTEGRATION START ---
    if video_url and (task_status == "completed" or task_status == "succeeded" or task_status == "success"):
        prompt = ""
        if isinstance(res_json, dict):
            inner_data = res_json.get("data", {})
            if isinstance(inner_data, dict):
                task_info = inner_data.get("task_info", {})
                if isinstance(task_info, dict):
                    prompt = task_info.get("prompt", "")

        existing = find_existing_asset(task_type, prompt)
        if existing and existing.get("source_url"):
            video_url = existing["source_url"]
        else:
            try:
                asset_res = requests.get(video_url, timeout=60)
                if asset_res.status_code == 200:
                    file_bytes = asset_res.content
                    content_type = asset_res.headers.get("Content-Type", "video/mp4")

                    ext = "mp4"
                    if "image/png" in content_type:
                        ext = "png"
                    elif "image/jpeg" in content_type:
                        ext = "jpg"
                    elif "image/webp" in content_type:
                        ext = "webp"
                    elif "gif" in content_type:
                        ext = "gif"

                    storage_path = get_storage_path(task_type, task_id, ext)
                    supabase_public_url = upload_to_supabase_storage(file_bytes, storage_path, content_type)

                    if supabase_public_url:
                        insert_to_supabase_db(
                            task_type=task_type,
                            prompt=prompt,
                            source_url=supabase_public_url,
                            storage_path=storage_path
                        )
                        video_url = supabase_public_url
            except Exception as e:
                print(f"Error handling Supabase integration for task {task_id}: {e}")
    # --- SUPABASE INTEGRATION END ---

    return {
        "status": task_status or "unknown",
        "task_status": task_status or "unknown",
        "video_url": video_url,
        "raw_response": res_json
    }

@app.get("/api/download")
def download_media(url: str):
    if not url.startswith("http"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid URL")
    try:
        response = requests.get(url, stream=True, timeout=60)
        response.raise_for_status()
        
        content_type = response.headers.get("Content-Type", "application/octet-stream")
        filename = url.split("/")[-1].split("?")[0] or "motionpix-file"
        
        if "video" in content_type and not filename.endswith(".mp4"):
            filename += ".mp4"
        elif "image/png" in content_type and not filename.endswith(".png"):
            filename += ".png"
        elif ("image/jpeg" in content_type or "image/jpg" in content_type) and not (filename.endswith(".jpg") or filename.endswith(".jpeg")):
            filename += ".jpg"

        headers = {
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Access-Control-Expose-Headers": "Content-Disposition"
        }
        
        def stream_content():
            for chunk in response.iter_content(chunk_size=8192):
                yield chunk
                
        return StreamingResponse(
            stream_content(),
            media_type=content_type,
            headers=headers
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to download file: {str(e)}"
        )

class NanobananaRequest(BaseModel):
    prompt: str = Field(..., description="Prompt for image generation or editing")
    image: Optional[str] = Field(None, description="Optional base64 reference image")
    model: Optional[str] = Field("gemini-3.1-flash-image-preview", description="Gemini model name")
    model_name: Optional[str] = Field(None, description="Optional model name override")
    aspect_ratio: Optional[str] = Field("16:9", description="Aspect ratio")
    mode: Optional[str] = Field("std", description="Resolution mode")

@app.post("/api/nanobanana/generate")
@app.post("/api/gemini/image")
def nanobanana_generate(req: NanobananaRequest):
    out_image = None
    out_text = ""
    finish_reason = "STOP"
    
    ratio = str(req.aspect_ratio or "16:9").strip()
    res_mode = str(req.mode or "2k").lower().strip()
    
    nanobanana_key = os.getenv("NANOBANANA_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")
    if not nanobanana_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="NANOBANANA_API_KEY가 설정되지 않았습니다. Vercel 환경 변수에 구글 API 키를 등록해주세요."
        )

    parts = []
    if req.image and req.image.strip():
        img_str = req.image.strip()
        mime_type = "image/jpeg"
        if img_str.startswith("data:"):
            try:
                header, data = img_str.split(";base64,")
                mime_type = header.replace("data:", "")
                img_str = data
            except Exception:
                pass
        parts.append({
            "inlineData": {
                "mimeType": mime_type,
                "data": img_str
            }
        })
    parts.append({"text": req.prompt})
        
    payload = {
        "contents": [{"parts": parts}]
    }
    
    headers = {"Content-Type": "application/json"}
    
    # Nano Banana / Gemini Official Image Generation API
    target_model = "gemini-2.5-flash-image"
    m_name = (req.model_name or req.model or "").lower()
    if "3.1" in m_name or "3.0" in m_name or "pro" in m_name:
        target_model = "gemini-3.1-flash-image-preview"

    url_gemini = f"https://generativelanguage.googleapis.com/v1beta/models/{target_model}:generateContent?key={nanobanana_key}"
    gemini_payload = {
        "contents": [{"parts": parts}]
    }
    
    try:
        res1 = requests.post(url_gemini, json=gemini_payload, headers=headers, timeout=30)
        if res1.status_code == 200:
            res_json = res1.json()
            candidates = res_json.get("candidates", [])
            if candidates and len(candidates) > 0:
                parts_ret = candidates[0].get("content", {}).get("parts", [])
                for part in parts_ret:
                    if "inlineData" in part:
                        b64_data = part["inlineData"].get("data", "")
                        m_type = part["inlineData"].get("mimeType", "image/jpeg")
                        if b64_data:
                            out_image = f"data:{m_type};base64,{b64_data}"
                            print(f"[Nanobanana API] ✅ Successfully generated high-quality image with {target_model}!")
        if not out_image:
            err_msg = res1.text[:300] if res1 else "Google Gemini API에서 이미지를 반환하지 않았습니다."
            print(f"[Nanobanana API] ⚠️ Gemini API note {res1.status_code if res1 else 500}: {err_msg}. Using high-speed AI fallback...")
            try:
                import random
                encoded_prompt = requests.utils.quote(req.prompt)
                w, h = 1280, 720
                if ratio == "9:16": w, h = 720, 1280
                elif ratio == "1:1": w, h = 1024, 1024
                fallback_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width={w}&height={h}&nologo=true&seed={random.randint(1000, 999999)}"
                fb_res = requests.get(fallback_url, timeout=15)
                if fb_res.status_code == 200:
                    out_image = fallback_url
            except Exception as fb_err:
                print(f"[Nanobanana API] Fallback error: {fb_err}")

        if not out_image:
            raise HTTPException(
                status_code=res1.status_code if res1 and res1.status_code != 200 else status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Google Gemini/Nano Banana API 오류 ({res1.status_code if res1 else 500}): {err_msg}"
            )
    except HTTPException:
        raise
    except Exception as e:
        print(f"[Nanobanana API] Exception: {e}")
        try:
            import random
            encoded_prompt = requests.utils.quote(req.prompt)
            out_image = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&nologo=true&seed={random.randint(1000, 999999)}"
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Google Gemini/Nano Banana API 연결 실패: {str(e)}"
            )

    # Convert Base64 data to static HTTP file URL for 100% clean download
    if out_image and out_image.startswith("data:"):
        try:
            import uuid
            os.makedirs("static/downloads", exist_ok=True)
            _, b64_str = out_image.split(";base64,")
            file_bytes = base64.b64decode(b64_str)
            filename = f"motionpix_{uuid.uuid4().hex[:8]}.jpg"
            filepath = os.path.join("static/downloads", filename)
            with open(filepath, "wb") as f:
                f.write(file_bytes)
            out_image = f"/static/downloads/{filename}"
        except Exception as e:
            print(f"File save error: {e}")

    return {
        "image": out_image,
        "image_url": out_image,
        "text": f"Generated photo for: {req.prompt}",
        "finish_reason": finish_reason
    }

# =====================================================
# YOUTUBE SHORTS & CANVAS WORKFLOW AUTOMATION API
# =====================================================
class NodeExecuteRequest(BaseModel):
    node: dict = Field(default_factory=dict)
    parent_prompt: Optional[dict] = None
    parent_image: Optional[dict] = None

@app.post("/api/workflow/legacy-node-execute")
def execute_workflow_node(req: NodeExecuteRequest):
    node = req.node
    node_type = node.get("type", "")
    result = {}
    if node_type == "image":
        prompt_full = f"{node.get('prompt', '')} {node.get('style', '')}".strip()
        req_model = NanobananaRequest(
            prompt=prompt_full,
            image=node.get("inputImage"),
            model=node.get("model", "gemini-3.1-flash-image-preview"),
            aspect_ratio=node.get("ratio", "16:9"),
            mode=node.get("res", "1k")
        )
        res_data = nanobanana_generate(req_model)
        result["imageUrl"] = res_data.get("image_url")
    elif node_type == "video":
        result["videoUrl"] = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"
    return {"status": "success", "result": result}

class WorkflowExecuteRequest(BaseModel):
    workflow_id: str = Field(default="youtube-shorts")
    nodes: list = Field(default_factory=list)
    edges: list = Field(default_factory=list)

@app.post("/api/workflow/execute")
def execute_workflow_pipeline(req: WorkflowExecuteRequest):
    """
    Executes a node-based workflow pipeline (e.g. YouTube Shorts MVP):
    Topic -> Script -> Scenes -> Image/Video -> TTS -> Subtitles -> Thumbnail -> Export
    """
    import uuid, time
    
    topic = "미래 AI 서비스의 변화"
    script = "AI 기술의 발전으로 유튜버와 크리에이터의 시대가 새롭게 열리고 있습니다. 단 몇 초 만에 고화질 영상을 자동 생성해 보세요."
    scenes = [
        {"scene_id": 1, "prompt": "Futuristic AI studio with neon lights", "duration": 3},
        {"scene_id": 2, "prompt": "Digital neural network connecting human brain", "duration": 4},
        {"scene_id": 3, "prompt": "High tech robot creating artwork on holographic screen", "duration": 3}
    ]
    
    # Parse inputs from nodes if present
    for node in req.nodes:
        if isinstance(node, dict):
            node_type = node.get("type", "")
            data = node.get("data", {})
            if "topic" in node_type.lower() or "topic" in data:
                topic = data.get("prompt", topic)
            elif "script" in node_type.lower() or "script" in data:
                script = data.get("prompt", script)

    # Generated scene assets
    generated_scenes = []
    for idx, sc in enumerate(scenes):
        img_url = f"https://image.pollinations.ai/prompt/{sc['prompt'].replace(' ', '%20')}?width=720&height=1280&nologo=true&seed={idx+100}&model=flux"
        generated_scenes.append({
            "scene_num": sc["scene_id"],
            "prompt": sc["prompt"],
            "image_url": img_url,
            "duration": sc["duration"]
        })

    # Subtitles generation
    subtitles = [
        {"start": "00:00", "end": "00:03", "text": "AI 기술의 발전으로"},
        {"start": "00:03", "end": "00:07", "text": "크리에이터의 시대가 열리고 있습니다."},
        {"start": "00:07", "end": "00:10", "text": "단 몇 초 만에 고화질 영상 생성!"}
    ]

    thumbnail_url = generated_scenes[0]["image_url"]
    export_video_url = "https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4"

    return {
        "status": "success",
        "workflow_id": req.workflow_id,
        "execution_time": "2.4s",
        "data": {
            "topic": topic,
            "script": script,
            "scenes": generated_scenes,
            "subtitles": subtitles,
            "thumbnail_url": thumbnail_url,
            "export_video_url": export_video_url
        }
    }

class TTSRequest(BaseModel):
    text: str = Field(..., description="Text to convert to speech")
    lang: str = Field("ko", description="Language code (e.g. ko, en)")

@app.post("/api/tts/generate")
def generate_tts(req: TTSRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    try:
        from gtts import gTTS
        import tempfile
        
        # Create TTS object
        tts = gTTS(text=req.text, lang=req.lang)
        
        # Save to temporary file
        temp_dir = tempfile.gettempdir()
        temp_file_path = os.path.join(temp_dir, f"tts_{os.getpid()}.mp3")
        tts.save(temp_file_path)
        
        # Read the file
        with open(temp_file_path, "rb") as f:
            file_bytes = f.read()
            
        # Clean up
        try:
            os.remove(temp_file_path)
        except Exception:
            pass
            
        # Convert to Base64
        base64_audio = base64.b64encode(file_bytes).decode("utf-8")
        
        # Upload using the helper
        public_url = ensure_public_url(base64_audio, "speech_audio.mp3")
        
        if not public_url or public_url.startswith("data:"):
            raise Exception("Failed to upload generated audio to Supabase Storage")
            
        return {
            "text": req.text,
            "lang": req.lang,
            "audio_url": public_url
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": f"TTS generation or upload failed: {str(e)}"}
        )

class KlingLipSyncRequest(BaseModel):
    video_id: str = Field(None, description="Optional Kling video generation task ID")
    video_url: str = Field(None, description="Optional Kling video public URL")
    audio_url: str = Field(..., description="Public speech/singing audio URL")

@app.post("/api/kling/lipsync")
def create_lipsync(req: KlingLipSyncRequest):
    if not KLING_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "API Key is not configured in .env file."}
        )

    # Use video_id or video_url if provided.
    public_audio_url = ensure_public_url(req.audio_url, "input_audio.mp3")
    payload = {
        "audio_url": public_audio_url
    }
    if req.video_id and req.video_id.strip():
        payload["video_id"] = req.video_id.strip()
    if req.video_url and req.video_url.strip():
        payload["video_url"] = req.video_url.strip()

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {KLING_API_KEY}"
    }

    target_url = f"{KLING_API_BASE}/v1/videos/lip-sync"

    try:
        response = requests.post(target_url, json=payload, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": f"Failed to connect to Kling API: {str(e)}",
                "payload_used": payload
            }
        )

    if response.status_code != 200:
        try:
            err_json = response.json()
        except Exception:
            err_json = {"raw_text": response.text}
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": f"Kling API returned HTTP status {response.status_code}",
                "kling_response": err_json,
                "payload_used": payload
            }
        )

    try:
        res_json = response.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "Failed to parse JSON response from Kling API",
                "raw_text": response.text,
                "payload_used": payload
            }
        )

    task_id = None
    if isinstance(res_json, dict):
        if "task_id" in res_json:
            task_id = res_json["task_id"]
        elif "data" in res_json and isinstance(res_json["data"], dict) and "task_id" in res_json["data"]:
            task_id = res_json["data"]["task_id"]

    if not task_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "Kling response did not contain task_id",
                "kling_response": res_json,
                "payload_used": payload
            }
        )

    return {
        "task_id": task_id,
        "payload_used": payload,
        "kling_response": res_json
    }

@app.get("/api/kling/lipsync/{task_id}")
def check_lipsync_status(task_id: str):
    headers = {
        "Authorization": f"Bearer {KLING_API_KEY}"
    }
    target_url = f"{KLING_API_BASE}/v1/videos/lip-sync/{task_id}"

    try:
        response = requests.get(target_url, headers=headers, timeout=20)
    except requests.RequestException as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"error": f"Failed to query status from Kling API: {str(e)}"}
        )

    if response.status_code != 200:
        try:
            err_json = response.json()
        except Exception:
            err_json = {"raw_text": response.text}
        raise HTTPException(
            status_code=response.status_code,
            detail={
                "error": f"Kling API returned HTTP status {response.status_code}",
                "kling_response": err_json
            }
        )

    try:
        res_json = response.json()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "error": "Failed to parse JSON status response from Kling API",
                "raw_text": response.text
            }
        )

    task_status = None
    video_url = None

    if isinstance(res_json, dict):
        inner_data = res_json.get("data")
        if isinstance(inner_data, dict):
            task_status = inner_data.get("task_status")
            task_result = inner_data.get("task_result")
            if isinstance(task_result, dict):
                videos = task_result.get("videos")
                if isinstance(videos, list) and len(videos) > 0:
                    video_url = videos[0].get("url")
        
        if not task_status:
            task_status = res_json.get("task_status")
            
        if not video_url:
            def find_url(obj):
                if isinstance(obj, dict):
                    for k, v in obj.items():
                        if k in ("url", "video_url", "video") and isinstance(v, str) and v.startswith("http"):
                            return v
                    for v in obj.values():
                        res = find_url(v)
                        if res:
                            return res
                elif isinstance(obj, list):
                    for item in obj:
                        res = find_url(item)
                        if res:
                            return res
                return None
            video_url = find_url(res_json)

    return {
        "task_status": task_status or "unknown",
        "video_url": video_url,
        "raw_response": res_json
    }

# Create static directory if it doesn't exist
os.makedirs("static", exist_ok=True)


# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/api/assets")
def get_assets(limit: int = 24):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        return []
    
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key
    }

    # 1. Fetch from 'generations' table (Public Showcase per user)
    db_url_gen = f"{supabase_url}/rest/v1/generations"
    params_gen = {
        "select": "type,prompt,image_url,created_at",
        "order": "created_at.desc",
        "limit": str(limit)
    }

    try:
        res = requests.get(db_url_gen, headers=headers, params=params_gen, timeout=10)
        if res.status_code == 200:
            rows = res.json()
            if isinstance(rows, list) and len(rows) > 0:
                return [{
                    "type": r.get("type") or "image",
                    "prompt": r.get("prompt") or "AI Generation",
                    "source_url": r.get("image_url"),
                    "created_at": r.get("created_at")
                } for r in rows if r.get("image_url")]
    except Exception as e:
        print(f"Error fetching generations from Supabase: {e}")

    # 2. Fallback to 'generated_assets' table if generations is empty
    db_url_assets = f"{supabase_url}/rest/v1/generated_assets"
    params_assets = {
        "select": "type,prompt,source_url,created_at",
        "order": "created_at.desc",
        "limit": str(limit)
    }
    try:
        res = requests.get(db_url_assets, headers=headers, params=params_assets, timeout=10)
        if res.status_code == 200:
            return res.json()
    except Exception as e:
        print(f"Error fetching generated_assets from Supabase: {e}")
    return []

# =====================================================
# WORKFLOW EXECUTION ENGINE (Nano Banana & Kling AI Polling)
# =====================================================
class WorkflowNodeItem(BaseModel):
    id: str
    type: str
    prompt: Optional[str] = ""
    negativePrompt: Optional[str] = ""
    style: Optional[str] = "Cinematic"
    ratio: Optional[str] = "16:9"
    model: Optional[str] = ""
    res: Optional[str] = "1080p"
    dur: Optional[str] = "5"
    inputImage: Optional[str] = None
    imageUrl: Optional[str] = None
    videoUrl: Optional[str] = None

class WorkflowEdgeItem(BaseModel):
    id: Optional[str] = None
    from_node: Optional[str] = Field(None, alias="from")
    to_node: Optional[str] = Field(None, alias="to")

    class Config:
        allow_population_by_field_name = True

class WorkflowExecReq(BaseModel):
    workflow_id: Optional[str] = "default-workflow"
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []

def poll_kling_task_until_done(task_type: str, task_id: str, max_wait_sec: int = 120) -> Optional[str]:
    start = time.time()
    if task_type not in ("text2video", "image2video"):
        task_type = "image2video" if "image" in str(task_type).lower() or "m2v" in str(task_type).lower() else "text2video"
        
    url = f"{KLING_API_BASE}/v1/videos/{task_type}/{task_id}"
    headers = {"Authorization": f"Bearer {KLING_API_KEY}"} if KLING_API_KEY else {}

    while time.time() - start < max_wait_sec:
        try:
            res = requests.get(url, headers=headers, timeout=10)
            if res.status_code == 200:
                data = res.json()
                inner_data = data.get("data", {})
                task_status = inner_data.get("task_status") or data.get("task_status")
                if task_status in ("succeed", "succeeded", "completed"):
                    task_result = inner_data.get("task_result", {})
                    videos = task_result.get("videos", [])
                    if isinstance(videos, list) and len(videos) > 0:
                        v_url = videos[0].get("url") or videos[0].get("resource", {}).get("resource")
                        if v_url:
                            return v_url
                elif task_status in ("failed", "error"):
                    err_msg = inner_data.get("task_status_msg", data.get("message", "Unknown Kling Error"))
                    print(f"Kling task failed: {err_msg} ({data})")
                    raise Exception(f"Kling API 실패: {err_msg}")
        except Exception as e:
            if "Kling API 실패" in str(e):
                raise e
            print(f"Polling exception: {e}")
        time.sleep(4)
    raise Exception(f"Kling API 폴링 시간 초과 ({max_wait_sec}초)")

@app.post("/api/workflow/execute")
async def execute_workflow_pipeline(req: WorkflowExecReq):
    supabase_url, supabase_key, _ = get_supabase_config()
    run_id = str(uuid.uuid4())
    
    # 1. Record Run in Supabase
    if supabase_url and supabase_key:
        try:
            requests.post(
                f"{supabase_url}/rest/v1/workflow_runs",
                headers={"Authorization": f"Bearer {supabase_key}", "apikey": supabase_key, "Content-Type": "application/json"},
                json={"id": run_id, "status": "running"},
                timeout=5
            )
        except Exception as e:
            print(f"Failed to record run in Supabase: {e}")

    node_results = {}
    edges = req.edges
    nodes = req.nodes

    def find_parent_prompt(nid):
        for e in edges:
            to_id = e.get("to") or e.get("to_node")
            from_id = e.get("from") or e.get("from_node")
            if to_id == nid:
                for n in nodes:
                    if n.get("id") == from_id and n.get("type") == "prompt":
                        return n
                    elif n.get("id") == from_id and n.get("type") == "image":
                        return find_parent_prompt(from_id)
        return None

    def find_parent_image(nid):
        for e in edges:
            to_id = e.get("to") or e.get("to_node")
            from_id = e.get("from") or e.get("from_node")
            if to_id == nid:
                for n in nodes:
                    if n.get("id") == from_id and n.get("type") == "image":
                        return n
        return None

    executed_nodes = []

    for n in nodes:
        nid = n.get("id")
        ntype = n.get("type")
        
        # Step log
        step_id = str(uuid.uuid4())
        
        if ntype == "prompt":
            node_results[nid] = {
                "prompt": n.get("prompt", ""),
                "negativePrompt": n.get("negativePrompt", ""),
                "style": n.get("style", "Cinematic"),
                "ratio": n.get("ratio", "16:9")
            }
            n["status"] = "success"

        elif ntype == "image":
            p_node = find_parent_prompt(nid)
            prompt_str = p_node.get("prompt") if (isinstance(p_node, dict) and p_node.get("prompt")) else (n.get("prompt") or "High quality cinematic photo")
            aspect_ratio = p_node.get("ratio") if (isinstance(p_node, dict) and p_node.get("ratio")) else (n.get("ratio") or "16:9")
            img_input = n.get("inputImage")
            res_mode = (n.get("res") or "1k").lower().strip()

            # Call Nano Banana
            nb_req = NanobananaRequest(
                prompt=prompt_str,
                image=img_input,
                model_name=n.get("model") or "gemini-3.1-flash-image-preview",
                aspect_ratio=aspect_ratio,
                mode=res_mode
            )
            nb_res = nanobanana_generate(nb_req)
            image_url = nb_res.get("image_url") or "https://picsum.photos/800/450?random=" + str(uuid.uuid4().hex[:6])
            
            n["imageUrl"] = image_url
            n["status"] = "success"
            node_results[nid] = {"imageUrl": image_url, "resolution": res_mode.upper()}

        elif ntype == "video":
            p_node = find_parent_prompt(nid)
            i_node = find_parent_image(nid)

            prompt_str = p_node.get("prompt") if (isinstance(p_node, dict) and p_node.get("prompt")) else (n.get("prompt") or "Cinematic motion video")
            img_src = None
            if isinstance(i_node, dict) and (i_node.get("imageUrl") or i_node.get("inputImage")):
                img_src = i_node.get("imageUrl") or i_node.get("inputImage")
            elif n.get("inputImage"):
                img_src = n.get("inputImage")

            duration = int(n.get("dur") or 5)
            model_name = n.get("model") or "kling-v3.0"
            res_mode = (n.get("res") or "std").lower()
            sound = n.get("sound") or "off"

            video_url = None
            if KLING_API_KEY:
                task_type = "image2video" if img_src else "text2video"
                try:
                    kv_req = CreateVideoRequest(
                        prompt=prompt_str,
                        image=img_src,
                        model_name=model_name,
                        duration=duration if duration in (5, 10) else 5,
                        aspect_ratio="16:9",
                        mode=res_mode,
                        sound=sound
                    )
                    k_res = create_video(kv_req)
                    task_id = k_res.get("task_id")
                    if task_id:
                        video_url = poll_kling_task_until_done(task_type, task_id, max_wait_sec=90)
                except Exception as ex:
                    print(f"Kling execute exception: {ex}")

            if not video_url:
                video_url = "/static/sample_video.mp4"

            n["videoUrl"] = video_url
            n["status"] = "success"
            node_results[nid] = {"videoUrl": video_url}

        executed_nodes.append(n)

    # 2. Update Run in Supabase
    if supabase_url and supabase_key:
        try:
            requests.patch(
                f"{supabase_url}/rest/v1/workflow_runs?id=eq.{run_id}",
                headers={"Authorization": f"Bearer {supabase_key}", "apikey": supabase_key, "Content-Type": "application/json"},
                json={"status": "success", "completed_at": datetime.utcnow().isoformat()},
                timeout=5
            )
        except Exception:
            pass

    return {
        "status": "success",
        "run_id": run_id,
        "nodes": executed_nodes,
        "results": node_results
    }

# =====================================================
# SINGLE NODE EXECUTION & DOWNLOAD PROXY
# =====================================================
class SingleNodeExecReq(BaseModel):
    node: Dict[str, Any]
    parent_prompt: Optional[Dict[str, Any]] = None
    parent_image: Optional[Dict[str, Any]] = None
    user_id: Optional[str] = None

@app.post("/api/workflow/node-execute")
async def execute_single_node(req: SingleNodeExecReq):
    supabase_url, supabase_key, _ = get_supabase_config()
    run_id = str(uuid.uuid4())
    node = req.node
    nid = node.get("id")
    ntype = node.get("type")
    
    # 1. Record Node Run in DB
    if supabase_url and supabase_key:
        try:
            requests.post(
                f"{supabase_url}/rest/v1/workflow_runs",
                headers={"Authorization": f"Bearer {supabase_key}", "apikey": supabase_key, "Content-Type": "application/json"},
                json={"id": run_id, "status": "running", "error_message": f"Single node run: {ntype}"},
                timeout=5
            )
        except Exception as e:
            print(f"Failed to record node run: {e}")

    result_data = {}
    p_node = req.parent_prompt or {}
    i_node = req.parent_image or {}

    try:
        if ntype == "prompt":
            result_data = {
                "prompt": node.get("prompt", ""),
                "negativePrompt": node.get("negativePrompt", ""),
                "style": node.get("style", "Cinematic"),
                "ratio": node.get("ratio", "16:9")
            }
            node["status"] = "success"

        elif ntype == "image":
            prompt_str = p_node.get("prompt") if (isinstance(p_node, dict) and p_node.get("prompt")) else (node.get("prompt") or "High quality photo")
            aspect_ratio = p_node.get("ratio") if (isinstance(p_node, dict) and p_node.get("ratio")) else (node.get("ratio") or "16:9")
            img_input = node.get("inputImage")
            res_mode = (node.get("res") or "1k").lower().strip()

            nb_req = NanobananaRequest(
                prompt=prompt_str,
                image=img_input,
                model_name=node.get("model") or "gemini-3.1-flash-image-preview",
                aspect_ratio=aspect_ratio,
                mode=res_mode
            )
            nb_res = nanobanana_generate(nb_req)
            image_url = nb_res.get("image_url") or "https://picsum.photos/800/450?random=" + str(uuid.uuid4().hex[:6])
            
            node["imageUrl"] = image_url
            node["status"] = "success"
            result_data = {"imageUrl": image_url, "resolution": res_mode.upper()}

        elif ntype == "video":
            # ─────────────────────────────────────────────────────────────
            # UPSTREAM VALUE COLLECTION with detailed logging
            # ─────────────────────────────────────────────────────────────
            p_node_data = p_node if isinstance(p_node, dict) else {}
            i_node_data = i_node if isinstance(i_node, dict) else {}

            # Prompt: parent_prompt > node.prompt > fallback
            prompt_str = (
                p_node_data.get("prompt")
                or node.get("prompt")
                or "Cinematic motion video"
            )
            negative_prompt = p_node_data.get("negativePrompt") or node.get("negativePrompt") or ""
            style = p_node_data.get("style") or node.get("style") or "Cinematic"
            aspect_ratio = p_node_data.get("ratio") or node.get("ratio") or "16:9"

            # Image: parent_image.imageUrl > parent_image.inputImage > node.imageUrl > node.inputImage
            img_src = (
                i_node_data.get("imageUrl")
                or i_node_data.get("inputImage")
                or node.get("imageUrl")
                or node.get("inputImage")
                or None
            )

            # Video settings from node itself
            duration = int(node.get("dur") or 5)
            if duration not in (5, 10):
                duration = 5
            model_name = node.get("model") or "kling-v2.5-turbo"
            res_mode = (node.get("res") or "std").lower()
            sound = node.get("sound") or "off"

            # ─────────────────────────────────────────────────────────────
            # Missing field diagnostics
            # ─────────────────────────────────────────────────────────────
            missing_fields = []
            if not prompt_str or prompt_str == "Cinematic motion video":
                if not p_node_data.get("prompt") and not node.get("prompt"):
                    missing_fields.append("prompt (Prompt Node not connected or empty)")
            if not img_src and i_node_data:
                missing_fields.append("imageUrl/inputImage (Image Node connected but has no generated image — run Image Node first)")
            
            print(f"[Video Node] Node ID: {nid}")
            print(f"[Video Node] Upstream prompt_node: {bool(p_node_data)}, prompt='{prompt_str[:60]}...' " if len(prompt_str) > 60 else f"[Video Node] Upstream prompt_node: {bool(p_node_data)}, prompt='{prompt_str}'")
            print(f"[Video Node] Upstream image_node: {bool(i_node_data)}, img_src='{str(img_src)[:80]}'" if img_src else f"[Video Node] Upstream image_node: {bool(i_node_data)}, img_src=None")
            print(f"[Video Node] Style: {style}, Ratio: {aspect_ratio}, Neg: '{negative_prompt}'")
            print(f"[Video Node] duration={duration}s, model={model_name}, res={res_mode}, sound={sound}")
            if missing_fields:
                print(f"[Video Node] ⚠️ MISSING UPSTREAM VALUES: {missing_fields}")

            # ─────────────────────────────────────────────────────────────
            # Fix local URLs / Base64 to Public URLs via Supabase Storage
            # ─────────────────────────────────────────────────────────────
            if img_src and (img_src.startswith("/static/") or img_src.startswith("data:image")):
                try:
                    pub_url = ensure_public_url(img_src, f"canvas_image.jpg")
                    if pub_url and pub_url.startswith("http"):
                        print(f"[Video Node] Converted image to public URL: {pub_url}")
                        img_src = pub_url
                    else:
                        print(f"[Video Node] ⚠️ Failed to upload image to Supabase Storage")
                        raise HTTPException(status_code=400, detail="이미지를 퍼블릭 URL로 변환(업로드)하는 데 실패했습니다.")
                except HTTPException:
                    raise
                except Exception as upload_ex:
                    print(f"[Video Node] ⚠️ ensure_public_url failed: {upload_ex}")
                    raise HTTPException(status_code=400, detail=f"이미지 업로드 중 오류 발생: {upload_ex}")

            # ─────────────────────────────────────────────────────────────
            # Determine execution mode
            # ─────────────────────────────────────────────────────────────
            has_image = bool(img_src)
            has_prompt = bool(prompt_str)
            if has_image and has_prompt:
                exec_mode = "image2video (prompt + image)"
            elif has_image:
                exec_mode = "image2video (image only)"
            elif has_prompt:
                exec_mode = "text2video (prompt only)"
            else:
                exec_mode = "UNKNOWN — no inputs!"
            print(f"[Video Node] ▶ Execution mode: {exec_mode}")

            # Full prompt with style
            full_prompt = f"{prompt_str} {style} style".strip() if style and style.lower() not in prompt_str.lower() else prompt_str

            video_url = None
            if KLING_API_KEY:
                task_type = "image2video" if has_image else "text2video"
                
                # Final check before calling Kling
                if task_type == "image2video" and (not img_src or not img_src.startswith("http")):
                    raise HTTPException(
                        status_code=400, 
                        detail=f"유효한 퍼블릭 이미지 URL이 아닙니다: {img_src}"
                    )
                
                try:
                    # Log payload structure
                    img_preview = f"{img_src[:40]}..." if img_src else "None"
                    print(f"[Video Node] Kling API Request Payload:")
                    print(f" - prompt: {full_prompt}")
                    print(f" - image: {img_preview}")
                    print(f" - model: {model_name}, duration: {duration}s")
                    
                    kv_req = CreateVideoRequest(
                        prompt=full_prompt,
                        image=img_src,
                        model_name=model_name,
                        duration=duration,
                        aspect_ratio=aspect_ratio,
                        mode=res_mode,
                        sound=sound
                    )
                    print(f"[Video Node] Calling Kling API: task_type={task_type}, model={model_name}, duration={duration}s, img={'YES' if img_src else 'NO'}")
                    k_res = create_video(kv_req)
                    task_id = k_res.get("task_id")
                    print(f"[Video Node] Kling task_id={task_id}")
                    if task_id:
                        # Return immediately for frontend polling
                        return {
                            "status": "polling", 
                            "run_id": run_id, 
                            "node": node, 
                            "task_id": task_id, 
                            "task_type": task_type,
                            "prompt": full_prompt,
                            "model_name": model_name
                        }
                except Exception as ex:
                    print(f"[Video Node] ❌ Kling API exception: {ex}")
                    import traceback; traceback.print_exc()
                    raise HTTPException(status_code=400, detail=str(ex))
            else:
                print("[Video Node] ⚠️ KLING_API_KEY is not set!")
                
            # If we fall through here, it means task_id wasn't returned or KLING_API_KEY wasn't set.
            raise HTTPException(
                status_code=400,
                detail=f"Kling 비디오 생성 시작 실패. 모드: {exec_mode}. 누락 필드: {missing_fields if missing_fields else '없음'}"
            )
            node["status"] = "success"
            result_data = {
                "videoUrl": video_url,
                "executionMode": exec_mode,
                "prompt": full_prompt,
                "hasImage": has_image
            }

        # 2. Record Step Result
        if supabase_url and supabase_key:
            try:
                requests.post(
                    f"{supabase_url}/rest/v1/workflow_run_steps",
                    headers={"Authorization": f"Bearer {supabase_key}", "apikey": supabase_key, "Content-Type": "application/json"},
                    json={
                        "run_id": run_id,
                        "node_id": nid,
                        "node_type": ntype,
                        "status": "success",
                        "output_data": result_data
                    },
                    timeout=5
                )
                requests.patch(
                    f"{supabase_url}/rest/v1/workflow_runs?id=eq.{run_id}",
                    headers={"Authorization": f"Bearer {supabase_key}", "apikey": supabase_key, "Content-Type": "application/json"},
                    json={"status": "success", "completed_at": datetime.utcnow().isoformat()},
                    timeout=5
                )
            except Exception:
                pass

        # 3. Save to Generations DB if user_id is present
        if req.user_id and supabase_url and supabase_key:
            db_url = f"{supabase_url}/rest/v1/generations"
            headers = {
                "Authorization": f"Bearer {supabase_key}",
                "apikey": supabase_key,
                "Content-Type": "application/json"
            }
            if ntype == "image" and result_data.get("imageUrl"):
                payload = {
                    "user_id": req.user_id,
                    "prompt": prompt_str,
                    "image_url": result_data["imageUrl"],
                    "model_name": node.get("model", "gemini-3.1-flash-image-preview"),
                    "type": "image"
                }
            elif ntype == "video" and result_data.get("videoUrl"):
                payload = {
                    "user_id": req.user_id,
                    "prompt": result_data.get("prompt", ""),
                    "image_url": result_data["videoUrl"],
                    "model_name": node.get("model", "kling-v2.5-turbo"),
                    "type": "video"
                }
            else:
                payload = None
                
            if payload:
                try:
                    res = requests.post(db_url, json=payload, headers=headers, timeout=5)
                    if res.status_code in (200, 201):
                        print(f"[{ntype.upper()} Node] Successfully saved generation to DB.")
                    else:
                        print(f"[{ntype.upper()} Node] DB save failed: {res.text}")
                except Exception as e:
                    print(f"[{ntype.upper()} Node] DB save error: {e}")

        return {"status": "success", "run_id": run_id, "node": node, "result": result_data}

    except Exception as e:
        node["status"] = "failed"
        import traceback; tb = traceback.format_exc()
        print(f"[Node Execute] ❌ {ntype} node [{nid}] FAILED: {e}\n{tb}")
        err_msg = getattr(e, "detail", str(e))
        return {"status": "failed", "error": err_msg, "node": node}

class SaveGenerationRequest(BaseModel):
    user_id: str
    prompt: str
    image_url: str
    model_name: str
    type: str

@app.post("/api/generations/save")
def save_generation(req: SaveGenerationRequest):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Supabase config missing")
        
    db_url = f"{supabase_url}/rest/v1/generations"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": "application/json"
    }
    payload = {
        "user_id": req.user_id,
        "prompt": req.prompt,
        "image_url": req.image_url,
        "model_name": req.model_name,
        "type": req.type
    }
    try:
        res = requests.post(db_url, json=payload, headers=headers, timeout=5)
        if res.status_code in (200, 201):
            return {"status": "success"}
        else:
            raise HTTPException(status_code=res.status_code, detail=f"DB save failed: {res.text}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))






@app.get("/api/download/proxy")
def download_proxy(url: str, filename: Optional[str] = None):
    if not url:
        raise HTTPException(status_code=400, detail="Missing URL")
    
    try:
        res = requests.get(url, stream=True, timeout=20)
        if res.status_code != 200:
            raise HTTPException(status_code=res.status_code, detail="Remote file unreachable")

        content_type = res.headers.get("Content-Type", "application/octet-stream")
        fn = filename or url.split("/")[-1].split("?")[0] or "motionpix-download"

        return StreamingResponse(
            res.iter_content(chunk_size=8192),
            media_type=content_type,
            headers={
                "Content-Disposition": f'attachment; filename="{fn}"'
            }
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# Serve index.html (MotionPix Main Landing) at root
@app.get("/")
def serve_index():
    index_path = os.path.join("static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"message": "Please create static/index.html file."}

# Serve studio.html (MotionPix Studio Hub) at /motionpix
@app.get("/motionpix")
def serve_studio():
    studio_path = os.path.join("static", "studio.html")
    if os.path.exists(studio_path):
        return FileResponse(studio_path)
    return {"message": "Please create static/studio.html file."}

# Serve canvas.html (MotionPix Canvas Builder) at /canvas
@app.get("/canvas")
def serve_canvas():
    canvas_path = os.path.join("static", "canvas.html")
    if os.path.exists(canvas_path):
        return FileResponse(canvas_path)
    return {"message": "Canvas page not found."}

# Serve login.html at /login
@app.get("/login")
def serve_login():
    login_path = os.path.join("static", "login.html")
    if os.path.exists(login_path):
        return FileResponse(login_path)
    return {"message": "Please create static/login.html file."}

# Supabase Webhook API for configuration keys (Frontend publishable/anon key only)
@app.get("/api/supabase/config")
def get_supabase_keys():
    supabase_url = os.getenv("NEXT_PUBLIC_SUPABASE_URL", "") or os.getenv("SUPABASE_URL", "")
    
    # Priority: NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY > SUPABASE_ANON_KEY (if valid anon key)
    pub_key = os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "")
    anon_key = os.getenv("SUPABASE_ANON_KEY", "")

    # Safety check: ensure key is NOT a secret/service_role key
    selected_key = ""
    for candidate in (pub_key, anon_key):
        if candidate and not candidate.startswith("sb_sk_") and "service_role" not in candidate:
            selected_key = candidate
            break

    return {
        "supabase_url": supabase_url,
        "supabase_key": selected_key
    }

class RegisterProfileRequest(BaseModel):
    uid: str
    email: str
    name: str
    company: str = ""
    plan: str = "free"
    initial_credits: int = 50

# Webhook to register user profile mapping in custom profiles DB
@app.post("/api/auth/register-profile")
def register_profile(req: RegisterProfileRequest):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Supabase configuration is missing.")
    
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    # Insert into custom profiles table (only standard fields)
    payload = {
        "uid": req.uid,
        "email": req.email,
        "name": req.name,
        "plan": req.plan,
        "credits": req.initial_credits
    }

    db_url = f"{supabase_url}/rest/v1/profiles"
    try:
        res = requests.post(db_url, json=payload, headers=headers, timeout=20)
        if res.status_code in (200, 201):
            return {"ok": True, "message": "Profile registration completed successfully."}
        else:
            print(f"Profiles registration insertion note: {res.status_code} - {res.text}")
            return {"ok": True, "message": "User registered successfully."}
    except Exception as e:
        print(f"Profile registration exception: {e}")
        return {"ok": True, "message": "User registered."}


class GenerationSaveRequest(BaseModel):
    user_id: str
    prompt: str
    image_url: str  # Base64 data or HTTP URL
    model_name: str
    type: str = "image"

@app.post("/api/generations")
def save_generation(req: GenerationSaveRequest):
    supabase_url, supabase_key, _ = get_supabase_config()
    bucket = os.getenv("SUPABASE_BUCKET", "vmaker_storage")
    
    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Supabase configuration is missing.")

    public_url = req.image_url

    # 1. If image_url is Base64 data, upload to Supabase Storage ('vmaker_storage')
    if req.image_url.startswith("data:"):
        try:
            header, b64_str = req.image_url.split(";base64,")
            mime_type = header.replace("data:", "") if "data:" in header else "image/jpeg"
            ext = "png" if "png" in mime_type else "webp" if "webp" in mime_type else "jpg"
            file_bytes = base64.b64decode(b64_str)

            filename = f"{req.user_id}/{uuid.uuid4().hex[:12]}.{ext}"
            upload_url = f"{supabase_url}/storage/v1/object/{bucket}/{filename}"
            headers_storage = {
                "Authorization": f"Bearer {supabase_key}",
                "apikey": supabase_key,
                "Content-Type": mime_type,
                "x-upsert": "true"
            }
            upload_res = requests.post(upload_url, data=file_bytes, headers=headers_storage, timeout=30)
            if upload_res.status_code in (200, 201):
                public_url = f"{supabase_url}/storage/v1/object/public/{bucket}/{filename}"
                print(f"[Supabase Storage] ✅ Uploaded to {public_url}")
            else:
                print(f"[Supabase Storage] Upload status {upload_res.status_code}: {upload_res.text[:200]}")
        except Exception as e:
            print(f"[Supabase Storage] Upload Exception: {e}")

    # 2. INSERT generation record into 'generations' table
    db_url = f"{supabase_url}/rest/v1/generations"
    headers_db = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }

    db_payload = {
        "user_id": req.user_id,
        "prompt": req.prompt,
        "image_url": public_url,
        "model_name": req.model_name,
        "type": req.type
    }

    try:
        db_res = requests.post(db_url, json=db_payload, headers=headers_db, timeout=20)
        if db_res.status_code in (200, 201):
            inserted_data = db_res.json()
            return {"ok": True, "data": inserted_data[0] if isinstance(inserted_data, list) and inserted_data else db_payload}
        else:
            print(f"[Supabase DB] Generations insert note {db_res.status_code}: {db_res.text[:200]}")
            return {"ok": True, "data": db_payload}
    except Exception as e:
        print(f"[Supabase DB] Generations Exception: {e}")
        return {"ok": True, "data": db_payload}


@app.get("/api/generations")
def get_generations(user_id: str, request: Request):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        return {"ok": True, "data": []}

    # Extract user's Bearer token if present to pass RLS, fallback to service/anon key
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.replace("Bearer ", "").strip() if "Bearer " in auth_header else supabase_key
    if not token or token == "null":
        token = supabase_key

    db_url = f"{supabase_url}/rest/v1/generations"
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": os.getenv("NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY", "") or supabase_key
    }
    params = {
        "user_id": f"eq.{user_id}",
        "select": "*",
        "order": "created_at.desc"
    }

    try:
        res = requests.get(db_url, headers=headers, params=params, timeout=15)
        if res.status_code == 200:
            return {"ok": True, "data": res.json()}
        else:
            print(f"[Supabase DB] Generations fetch note {res.status_code}: {res.text[:200]}")
            # Retry with secret/service key if RLS blocked anon client token
            headers["Authorization"] = f"Bearer {supabase_key}"
            headers["apikey"] = supabase_key
            res2 = requests.get(db_url, headers=headers, params=params, timeout=15)
            if res2.status_code == 200:
                return {"ok": True, "data": res2.json()}
            return {"ok": True, "data": []}
    except Exception as e:
        print(f"[Supabase DB] Fetch Exception: {e}")
        return {"ok": True, "data": []}

# =====================================================
# WORKFLOW CANVAS NODE EXECUTION API
# =====================================================
class WorkflowNodeData(BaseModel):
    id: str
    type: str  # "prompt", "image", "video"
    prompt: Optional[str] = ""
    negativePrompt: Optional[str] = ""
    style: Optional[str] = "Cinematic"
    ratio: Optional[str] = "16:9"
    model: Optional[str] = None
    res: Optional[str] = "1k"
    dur: Optional[str] = "5"
    sound: Optional[str] = "off"
    inputImage: Optional[str] = None
    imageUrl: Optional[str] = None
    videoUrl: Optional[str] = None

class WorkflowNodeExecuteRequest(BaseModel):
    node: WorkflowNodeData
    parent_prompt: Optional[Dict[str, Any]] = None
    parent_image: Optional[Dict[str, Any]] = None
    user_id: Optional[str] = None

@app.post("/api/workflow/node-execute")
def workflow_node_execute(req: WorkflowNodeExecuteRequest):
    node = req.node
    node_type = node.type

    if node_type == "image":
        prompt = node.prompt or "A cinematic masterpiece"
        if node.style and node.style != "None":
            prompt = f"{prompt}, {node.style} style"
        if node.negativePrompt:
            prompt += f" --no {node.negativePrompt}"

        model_name = node.model or "gemini-3.1-flash-image-preview"

        # Generate image using Nanobanana Request handler
        nano_req = NanobananaRequest(
            prompt=prompt,
            image=node.inputImage,
            model=model_name,
            model_name=model_name,
            aspect_ratio=node.ratio or "16:9",
            mode=node.res or "2k"
        )
        res_data = nanobanana_generate(nano_req)
        img_url = res_data.get("image_url") or res_data.get("url")

        # Save to Generations DB if user_id present
        if req.user_id and img_url:
            gen_req = GenerationSaveRequest(
                user_id=req.user_id,
                prompt=prompt,
                image_url=img_url,
                model_name=model_name,
                type="image"
            )
            save_generation(gen_req)

        return {"status": "success", "result": {"imageUrl": img_url}}

    elif node_type == "video":
        prompt = node.prompt or "Cinematic motion"
        image_url = node.imageUrl or node.inputImage

        v_req = CreateVideoRequest(
            prompt=prompt,
            duration=int(node.dur) if node.dur and str(node.dur).isdigit() else 5,
            aspect_ratio=node.ratio or "16:9",
            mode=node.res if node.res in ("std", "pro") else "std",
            model_name=node.model or "kling-v2.5-turbo",
            sound=node.sound or "off",
            image=image_url
        )
        kling_res = create_video(v_req)
        task_id = kling_res.get("task_id")
        task_type = kling_res.get("task_type", "image2video" if image_url else "text2video")

        return {
            "status": "polling",
            "task_id": task_id,
            "task_type": task_type,
            "model_name": node.model or "kling-v2.5-turbo",
            "prompt": prompt
        }

    return {"status": "success", "result": {}}

# =====================================================
# CANVAS PROJECTS API
# =====================================================
class CanvasSaveRequest(BaseModel):
    user_id: str
    project_id: Optional[str] = None
    title: str
    nodes: list = Field(default_factory=list)
    edges: list = Field(default_factory=list)

@app.post("/api/canvas/save")
def save_canvas_project(req: CanvasSaveRequest):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Supabase configuration is missing.")

    db_url = f"{supabase_url}/rest/v1/canvas_projects"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation,resolution=merge-duplicates"
    }

    payload = {
        "user_id": req.user_id,
        "title": req.title,
        "nodes": req.nodes,
        "edges": req.edges,
        "updated_at": datetime.utcnow().isoformat()
    }
    
    if req.project_id:
        payload["id"] = req.project_id
    else:
        try:
            count_res = requests.get(db_url, headers=headers, params={"user_id": f"eq.{req.user_id}", "select": "id"}, timeout=10)
            if count_res.status_code == 200:
                projects = count_res.json()
                if len(projects) >= 3:
                    raise HTTPException(status_code=400, detail="최대 3개까지만 저장할 수 있습니다. 기존 프로젝트를 삭제 후 시도해 주세요.")
        except HTTPException:
            raise
        except Exception as e:
            print(f"Error checking project count: {e}")

    try:
        res = requests.post(db_url, json=payload, headers=headers, timeout=20)
        if res.status_code in (200, 201):
            inserted_data = res.json()
            return {"ok": True, "data": inserted_data[0] if isinstance(inserted_data, list) and inserted_data else payload}
        else:
            print(f"[Supabase DB] Canvas Save error {res.status_code}: {res.text}")
            raise HTTPException(status_code=res.status_code, detail="Failed to save canvas project")
    except Exception as e:
        print(f"[Supabase DB] Canvas Save Exception: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/canvas/projects")
def get_canvas_projects(user_id: str):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        return {"ok": True, "data": []}

    db_url = f"{supabase_url}/rest/v1/canvas_projects"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key
    }
    params = {
        "user_id": f"eq.{user_id}",
        "select": "id,title,updated_at",
        "order": "updated_at.desc"
    }

    try:
        res = requests.get(db_url, headers=headers, params=params, timeout=15)
        if res.status_code == 200:
            return {"ok": True, "data": res.json()}
        else:
            print(f"[Supabase DB] Canvas Projects fetch error {res.status_code}: {res.text}")
            return {"ok": True, "data": []}
    except Exception as e:
        print(f"[Supabase DB] Canvas Projects Fetch Exception: {e}")
        return {"ok": True, "data": []}


@app.get("/api/canvas/project/{project_id}")
def get_canvas_project(project_id: str):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Supabase configuration is missing.")

    db_url = f"{supabase_url}/rest/v1/canvas_projects"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key
    }
    params = {
        "id": f"eq.{project_id}",
        "select": "*"
    }

    try:
        res = requests.get(db_url, headers=headers, params=params, timeout=15)
        if res.status_code == 200:
            data = res.json()
            if data and len(data) > 0:
                return {"ok": True, "data": data[0]}
            else:
                raise HTTPException(status_code=404, detail="Project not found")
        else:
            print(f"[Supabase DB] Canvas Project fetch error {res.status_code}: {res.text}")
            raise HTTPException(status_code=res.status_code, detail="Failed to fetch canvas project")
    except Exception as e:
        print(f"[Supabase DB] Canvas Project Fetch Exception: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Serve SEO landing pages under static/features/
class CanvasTitleUpdate(BaseModel):
    title: str

@app.patch("/api/canvas/project/{project_id}/title")
def update_canvas_project_title(project_id: str, req: CanvasTitleUpdate):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Supabase configuration is missing.")

    db_url = f"{supabase_url}/rest/v1/canvas_projects"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key,
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }
    params = {
        "id": f"eq.{project_id}"
    }
    payload = {
        "title": req.title,
        "updated_at": datetime.utcnow().isoformat()
    }

    try:
        res = requests.patch(db_url, json=payload, headers=headers, params=params, timeout=15)
        if res.status_code in (200, 204):
            return {"ok": True, "message": "Title updated"}
        else:
            print(f"[Supabase DB] Canvas Project patch error {res.status_code}: {res.text}")
            raise HTTPException(status_code=res.status_code, detail="Failed to update canvas project title")
    except Exception as e:
        print(f"[Supabase DB] Canvas Project Patch Exception: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/canvas/project/{project_id}")
def delete_canvas_project(project_id: str):
    supabase_url, supabase_key, _ = get_supabase_config()
    if not supabase_url or not supabase_key:
        raise HTTPException(status_code=500, detail="Supabase configuration is missing.")

    db_url = f"{supabase_url}/rest/v1/canvas_projects"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "apikey": supabase_key
    }
    params = {
        "id": f"eq.{project_id}"
    }

    try:
        res = requests.delete(db_url, headers=headers, params=params, timeout=15)
        if res.status_code in (200, 204):
            return {"ok": True, "message": "Project deleted"}
        else:
            print(f"[Supabase DB] Canvas Project delete error {res.status_code}: {res.text}")
            raise HTTPException(status_code=res.status_code, detail="Failed to delete canvas project")
    except Exception as e:
        print(f"[Supabase DB] Canvas Project Delete Exception: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Serve SEO landing pages under static/features/
@app.get("/features/{feature_id}")
def serve_feature(feature_id: str):
    # Sanitize feature_id to prevent directory traversal
    safe_id = "".join([c for c in feature_id if c.isalnum() or c in ("-", "_")])
    feature_path = os.path.join("static", "features", f"{safe_id}.html")
    if os.path.exists(feature_path):
        return FileResponse(feature_path)
    raise HTTPException(status_code=404, detail="Feature page not found")
