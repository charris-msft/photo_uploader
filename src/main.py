"""
Secure FastAPI Photo Uploader
A production-ready FastAPI application for uploading photos to local storage.
"""

import os
import uuid
import json
import logging
import platform
from typing import List, Optional
from datetime import datetime
from pathlib import Path

import aiofiles
from fastapi import FastAPI, File, UploadFile, HTTPException, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def safe_log(level: str, message: str, emoji: str = "", alt_prefix: str = ""):
    """Log with emoji on non-Windows systems, plain text on Windows"""
    if platform.system() == 'Windows':
        prefix = f"[{alt_prefix}]" if alt_prefix else ""
        getattr(logger, level.lower())(f"{prefix} {message}")
    else:
        getattr(logger, level.lower())(f"{emoji} {message}")

# Configuration
class Config:
    """Application configuration"""
    
    def __init__(self):
        self.upload_dir = Path(os.getenv("UPLOAD_DIR", "uploads"))
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        
        # File upload limits
        self.max_file_size = 100 * 1024 * 1024  # 100MB
        self.allowed_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff'}
        self.allowed_mime_types = {
            'image/jpeg', 'image/png', 'image/gif', 'image/webp', 
            'image/bmp', 'image/tiff'
        }

config = Config()

# Initialize FastAPI app
app = FastAPI(
    title="🖼️ Secure Photo Uploader",
    description="A secure FastAPI application for uploading photos to local storage",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# Add CORS middleware for development (configure appropriately for production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],  # Add your frontend domains
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files and templates
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

class SecurePhotoUploader:
    """Secure photo uploader using local filesystem storage"""
    
    def __init__(self):
        self.config = config
        
    def _validate_file(self, file: UploadFile) -> None:
        """Validate uploaded file"""
        # Check file extension
        file_extension = Path(file.filename or "").suffix.lower()
        if file_extension not in self.config.allowed_extensions:
            raise HTTPException(
                status_code=400,
                detail=f"File type '{file_extension}' not allowed. "
                       f"Allowed types: {', '.join(self.config.allowed_extensions)}"
            )
        
        # Check MIME type
        if file.content_type not in self.config.allowed_mime_types:
            raise HTTPException(
                status_code=400,
                detail=f"MIME type '{file.content_type}' not allowed"
            )
    
    def _generate_blob_name(self, original_filename: str) -> str:
        """Generate a unique file path with timestamp and UUID"""
        timestamp = datetime.utcnow().strftime("%Y/%m/%d")
        file_extension = Path(original_filename).suffix.lower()
        unique_id = str(uuid.uuid4())[:8]
        
        # Clean filename
        clean_name = "".join(c for c in Path(original_filename).stem if c.isalnum() or c in ('-', '_'))
        clean_name = clean_name[:50]  # Limit length
        
        return f"{timestamp}/{clean_name}_{unique_id}{file_extension}"
    
    async def upload_photo(
        self, 
        file: UploadFile, 
        blob_name: Optional[str] = None,
        tags: Optional[dict] = None
    ) -> dict:
        """Upload photo to local storage"""
        try:
            # Validate file
            self._validate_file(file)
            
            # Generate file path if not provided
            if not blob_name:
                blob_name = self._generate_blob_name(file.filename or f"photo_{uuid.uuid4()}")
            
            # Read file content
            file_content = await file.read()
            file_size = len(file_content)
            
            # Check file size
            if file_size > self.config.max_file_size:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large. Maximum size: {self.config.max_file_size // 1024 // 1024}MB"
                )
            
            if file_size == 0:
                raise HTTPException(status_code=400, detail="Empty file not allowed")
            
            # Ensure target directory exists
            target_path = self.config.upload_dir / blob_name
            target_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write file to disk
            async with aiofiles.open(target_path, 'wb') as f:
                await f.write(file_content)
            
            # Build and write metadata sidecar
            metadata = {
                'original_filename': file.filename or 'unknown',
                'upload_timestamp': datetime.utcnow().isoformat(),
                'content_type': file.content_type or 'application/octet-stream',
                'file_size': str(file_size)
            }
            if tags:
                for key, value in tags.items():
                    metadata[f"tag_{key}"] = str(value)

            meta_path = target_path.with_suffix(target_path.suffix + '.meta.json')
            async with aiofiles.open(meta_path, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(metadata))
            
            safe_log("info", f"Successfully uploaded photo: {blob_name} ({file_size} bytes)", "✅", "SUCCESS")
            
            return {
                'success': True,
                'blob_name': blob_name,
                'blob_url': f"/api/photos/{blob_name}/image",
                'file_size': file_size,
                'content_type': file.content_type,
                'upload_timestamp': datetime.utcnow().isoformat()
            }
            
        except HTTPException:
            raise
        except Exception as e:
            safe_log("error", f"Unexpected error during upload: {e}", "❌", "ERROR")
            raise HTTPException(
                status_code=500,
                detail=f"Upload failed: {str(e)}"
            )
        finally:
            await file.seek(0)
    
    async def list_photos(self, limit: int = 50, prefix: str = "") -> List[dict]:
        """List photos in the upload directory"""
        try:
            photos = []
            search_root = self.config.upload_dir / prefix if prefix else self.config.upload_dir
            
            if not search_root.exists():
                return photos

            for file_path in sorted(search_root.rglob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
                if file_path.suffix.lower() not in self.config.allowed_extensions:
                    continue
                if not file_path.is_file():
                    continue
                
                blob_name = file_path.relative_to(self.config.upload_dir).as_posix()
                
                # Read sidecar metadata if available
                meta_path = file_path.with_suffix(file_path.suffix + '.meta.json')
                metadata = {}
                if meta_path.exists():
                    async with aiofiles.open(meta_path, 'r', encoding='utf-8') as f:
                        metadata = json.loads(await f.read())
                
                content_type = metadata.get('content_type', 'application/octet-stream')
                if not content_type.startswith('image/'):
                    continue

                stat = file_path.stat()
                photos.append({
                    'name': blob_name,
                    'size': stat.st_size,
                    'last_modified': datetime.utcfromtimestamp(stat.st_mtime).isoformat(),
                    'content_type': content_type,
                    'url': f"/api/photos/{blob_name}/image",
                    'metadata': metadata
                })
                
                if len(photos) >= limit:
                    break
            
            safe_log("info", f"Listed {len(photos)} photo files", "✅", "SUCCESS")
            return photos
            
        except Exception as e:
            safe_log("error", f"Error listing photos: {e}", "❌", "ERROR")
            raise HTTPException(status_code=500, detail=f"Failed to list photos: {str(e)}")
    
    async def get_photo_data(self, blob_name: str) -> tuple[bytes, str]:
        """Get photo data and content type from local storage"""
        try:
            file_path = self.config.upload_dir / blob_name
            if not file_path.exists() or not file_path.is_file():
                raise HTTPException(status_code=404, detail="Photo not found")
            
            async with aiofiles.open(file_path, 'rb') as f:
                content = await f.read()
            
            # Determine content type from sidecar or extension
            meta_path = file_path.with_suffix(file_path.suffix + '.meta.json')
            content_type = 'application/octet-stream'
            if meta_path.exists():
                async with aiofiles.open(meta_path, 'r', encoding='utf-8') as f:
                    metadata = json.loads(await f.read())
                content_type = metadata.get('content_type', content_type)
            
            safe_log("info", f"Retrieved photo data for: {blob_name} ({len(content)} bytes)", "✅", "SUCCESS")
            return content, content_type
            
        except HTTPException:
            raise
        except Exception as e:
            safe_log("error", f"Error retrieving photo {blob_name}: {e}", "❌", "ERROR")
            raise HTTPException(status_code=500, detail=f"Failed to retrieve photo: {str(e)}")

    async def delete_photo(self, blob_name: str) -> bool:
        """Delete a photo from local storage"""
        try:
            file_path = self.config.upload_dir / blob_name
            if not file_path.exists() or not file_path.is_file():
                raise HTTPException(status_code=404, detail="Photo not found")
            
            file_path.unlink()
            
            # Remove sidecar if present
            meta_path = file_path.with_suffix(file_path.suffix + '.meta.json')
            if meta_path.exists():
                meta_path.unlink()
            
            safe_log("info", f"Successfully deleted photo: {blob_name}", "✅", "SUCCESS")
            return True
            
        except HTTPException:
            raise
        except Exception as e:
            safe_log("error", f"Error deleting photo {blob_name}: {e}", "❌", "ERROR")
            raise HTTPException(status_code=500, detail=f"Failed to delete photo: {str(e)}")

# Initialize the photo uploader
photo_uploader = SecurePhotoUploader()

# Routes
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page with photo upload form"""
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/upload")
async def upload_photo(
    request: Request,
    file: UploadFile = File(...),
    album: str = Form(""),
    description: str = Form("")
):
    """Upload photo endpoint"""
    try:
        # Prepare tags from form data
        tags = {}
        if album:
            tags['album'] = album
        if description:
            tags['description'] = description
        
        result = await photo_uploader.upload_photo(file, tags=tags)
        
        # Return success response
        return templates.TemplateResponse("upload_success.html", {
            "request": request,
            "result": result,
            "filename": file.filename
        })
        
    except HTTPException as e:
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": e.detail,
            "status_code": e.status_code
        })
    except Exception as e:
        safe_log("error", f"Unexpected error in upload endpoint: {e}", "❌", "ERROR")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "An unexpected error occurred",
            "status_code": 500
        })

@app.get("/api/photos")
async def get_photos(limit: int = 50, prefix: str = ""):
    """API endpoint to get photos list"""
    photos = await photo_uploader.list_photos(limit=limit, prefix=prefix)
    return JSONResponse({"photos": photos, "count": len(photos)})

@app.get("/api/photos/{blob_name:path}/image")
async def get_photo_image(blob_name: str):
    """API endpoint to serve photo image data"""
    try:
        content, content_type = await photo_uploader.get_photo_data(blob_name)
        return Response(content=content, media_type=content_type)
    except HTTPException:
        raise
    except Exception as e:
        safe_log("error", f"Error serving image {blob_name}: {e}", "❌", "ERROR")
        raise HTTPException(status_code=500, detail="Failed to serve image")

@app.get("/gallery", response_class=HTMLResponse)
async def photo_gallery(request: Request, limit: int = 20):
    """Photo gallery page"""
    try:
        photos = await photo_uploader.list_photos(limit=limit)
        return templates.TemplateResponse("gallery.html", {
            "request": request,
            "photos": photos
        })
    except Exception as e:
        safe_log("error", f"Error loading gallery: {e}", "❌", "ERROR")
        return templates.TemplateResponse("error.html", {
            "request": request,
            "error": "Failed to load photo gallery",
            "status_code": 500
        })

@app.delete("/api/photos/{blob_name:path}")
async def delete_photo_endpoint(blob_name: str):
    """API endpoint to delete a photo"""
    success = await photo_uploader.delete_photo(blob_name)
    return JSONResponse({"success": success, "message": f"Photo {blob_name} deleted successfully"})

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        upload_dir = config.upload_dir
        return {
            "status": "healthy",
            "upload_dir": str(upload_dir),
            "upload_dir_exists": upload_dir.exists(),
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        safe_log("error", f"Health check failed: {e}", "❌", "ERROR")
        return {"status": "unhealthy", "error": str(e), "timestamp": datetime.utcnow().isoformat()}

# Error handlers
@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": "Page not found",
        "status_code": 404
    })

@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    safe_log("error", f"Internal server error: {exc}", "❌", "ERROR")
    return templates.TemplateResponse("error.html", {
        "request": request,
        "error": "Internal server error",
        "status_code": 500
    })

if __name__ == "__main__":
    # Development server
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )
