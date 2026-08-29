import os
import secrets
import uuid
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Session, declarative_base, sessionmaker
from sqlalchemy import create_engine

# ===================================================================
# 1. CONFIGURATION (all from environment variables — nothing sensitive
#    is hardcoded here. See .env.example for the full list.)
# ===================================================================
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Local dev fallback only — never used in production. No real
    # credentials live in this file.
    DATABASE_URL = "sqlite:///./pcoss_dev.db"

ADMIN_KEY = os.getenv("ADMIN_KEY")  # shared password for admin-sermons.html

# Cloudflare R2 (S3-compatible) for gallery uploads
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "")  # e.g. https://pub-xxxx.r2.dev (no trailing slash)

# Resend (email) for contact form notifications
RESEND_API_KEY = os.getenv("RESEND_API_KEY")
RESEND_TO_EMAIL = os.getenv("RESEND_TO_EMAIL")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "PCOSS Website <onboarding@resend.dev>")

# Allowed frontend origins for CORS
ALLOWED_ORIGINS = [
    "https://pcoss-church.pages.dev",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://localhost:8000",
]
# Add a comma-separated CUSTOM_ORIGINS env var if you attach a custom domain later
if os.getenv("CUSTOM_ORIGINS"):
    ALLOWED_ORIGINS += [o.strip() for o in os.getenv("CUSTOM_ORIGINS").split(",") if o.strip()]

engine_kwargs = {"pool_pre_ping": True}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ===================================================================
# 2. DATABASE MODELS
# ===================================================================
class SermonDB(Base):
    __tablename__ = "sermons"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    preacher = Column(String, nullable=False)
    scripture = Column(String, nullable=True)
    youtube_url = Column(String, nullable=False)
    sermon_date = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class VerseDB(Base):
    __tablename__ = "verses"
    id = Column(Integer, primary_key=True, index=True)
    verse = Column(Text, nullable=False)
    reference = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class UpdateDB(Base):
    """Covers both 'announcement' and 'event' post types."""
    __tablename__ = "updates"
    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, nullable=False)  # "announcement" | "event"
    title = Column(String, nullable=False)
    date_display = Column(String, nullable=False)
    event_date = Column(String, nullable=True)  # ISO date string, used for sorting
    description = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class GalleryItemDB(Base):
    __tablename__ = "gallery_items"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    media_type = Column(String, nullable=False)  # "image" | "video"
    file_url = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class ContactMessageDB(Base):
    __tablename__ = "contact_messages"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    reason = Column(String, nullable=True)
    message = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


Base.metadata.create_all(bind=engine)

# ===================================================================
# 3. FASTAPI SETUP
# ===================================================================
app = FastAPI(title="PCOSS Church API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_origin_regex=r"https://.*\.pcoss-church\.pages\.dev",  # Cloudflare Pages preview deploys
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def verify_admin(x_admin_key: Optional[str] = Header(None)):
    if not ADMIN_KEY:
        raise HTTPException(status_code=500, detail="Server misconfigured: ADMIN_KEY is not set")
    if not x_admin_key or not secrets.compare_digest(x_admin_key, ADMIN_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")
    return True


# ===================================================================
# 4. SCHEMAS
# ===================================================================
class SermonCreate(BaseModel):
    title: str
    preacher: str
    scripture: Optional[str] = None
    youtube_url: str
    sermon_date: str


class SermonResponse(SermonCreate):
    id: int
    class Config:
        from_attributes = True


class VerseCreate(BaseModel):
    verse: str
    reference: str


class VerseResponse(VerseCreate):
    id: int
    class Config:
        from_attributes = True


class UpdateCreate(BaseModel):
    type: str  # "announcement" | "event"
    title: str
    date_display: str
    event_date: Optional[str] = None
    description: str


class UpdateResponse(UpdateCreate):
    id: int
    created_at: datetime
    class Config:
        from_attributes = True


class UpdatesFeed(BaseModel):
    events: List[UpdateResponse]
    announcements: List[UpdateResponse]


class GalleryResponse(BaseModel):
    id: int
    title: str
    description: Optional[str] = None
    media_type: str
    file_url: str
    created_at: datetime
    class Config:
        from_attributes = True


class ContactCreate(BaseModel):
    name: str
    email: str
    reason: Optional[str] = None
    message: str


# ===================================================================
# 5. HELPERS — R2 upload + Resend email (both best-effort / lazy-init
#    so the app still boots if these env vars aren't set yet)
# ===================================================================
def upload_to_r2(file: UploadFile) -> tuple[str, str]:
    if not all([R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET_NAME, R2_PUBLIC_URL]):
        raise HTTPException(status_code=500, detail="Server misconfigured: R2 storage env vars are not set")

    content_type = file.content_type or "application/octet-stream"
    if content_type.startswith("image/"):
        media_type = "image"
    elif content_type.startswith("video/"):
        media_type = "video"
    else:
        raise HTTPException(status_code=400, detail="Only image or video files are allowed")

    import boto3
    from botocore.client import Config as BotoConfig

    s3 = boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=BotoConfig(signature_version="s3v4"),
        region_name="auto",
    )

    ext = os.path.splitext(file.filename or "")[1]
    key = f"gallery/{uuid.uuid4().hex}{ext}"

    try:
        s3.upload_fileobj(file.file, R2_BUCKET_NAME, key, ExtraArgs={"ContentType": content_type})
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upload to storage failed: {e}")

    url = f"{R2_PUBLIC_URL.rstrip('/')}/{key}"
    return url, media_type


def send_contact_email(name: str, email: str, reason: Optional[str], message: str) -> None:
    if not RESEND_API_KEY or not RESEND_TO_EMAIL:
        return  # email notifications not configured — message is still saved to DB
    try:
        httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={
                "from": RESEND_FROM_EMAIL,
                "to": [RESEND_TO_EMAIL],
                "reply_to": email,
                "subject": f"New PCOSS website message from {name}",
                "text": f"From: {name} <{email}>\nReason: {reason or 'N/A'}\n\n{message}",
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[WARN] Failed to send contact notification email: {e}")


# ===================================================================
# 6. ENDPOINTS
# ===================================================================
@app.get("/")
def root():
    return {"message": "PCOSS Church API is running!"}


# --- Sermons ---
@app.get("/api/sermons", response_model=List[SermonResponse])
def get_sermons(db: Session = Depends(get_db)):
    return db.query(SermonDB).order_by(SermonDB.id.desc()).all()


@app.post("/api/sermons", response_model=SermonResponse, dependencies=[Depends(verify_admin)])
def create_sermon(sermon: SermonCreate, db: Session = Depends(get_db)):
    new_sermon = SermonDB(**sermon.model_dump())
    db.add(new_sermon)
    db.commit()
    db.refresh(new_sermon)
    return new_sermon


# --- Verses ---
@app.get("/api/verses", response_model=List[VerseResponse])
def get_verses(db: Session = Depends(get_db)):
    return db.query(VerseDB).order_by(VerseDB.id.desc()).all()


@app.post("/api/verses", response_model=VerseResponse, dependencies=[Depends(verify_admin)])
def create_verse(verse: VerseCreate, db: Session = Depends(get_db)):
    new_verse = VerseDB(**verse.model_dump())
    db.add(new_verse)
    db.commit()
    db.refresh(new_verse)
    return new_verse


# --- Updates (announcements + events) ---
@app.get("/api/updates", response_model=UpdatesFeed)
def get_updates(db: Session = Depends(get_db)):
    events = (
        db.query(UpdateDB)
        .filter(UpdateDB.type == "event")
        .order_by(func.coalesce(UpdateDB.event_date, "9999-12-31").asc())
        .all()
    )
    announcements = (
        db.query(UpdateDB)
        .filter(UpdateDB.type == "announcement")
        .order_by(UpdateDB.id.desc())
        .all()
    )
    return {"events": events, "announcements": announcements}


@app.post("/api/updates", response_model=UpdateResponse, dependencies=[Depends(verify_admin)])
def create_update(update: UpdateCreate, db: Session = Depends(get_db)):
    if update.type not in ("announcement", "event"):
        raise HTTPException(status_code=400, detail="type must be 'announcement' or 'event'")
    new_update = UpdateDB(**update.model_dump())
    db.add(new_update)
    db.commit()
    db.refresh(new_update)
    return new_update


# --- Gallery ---
@app.get("/api/gallery", response_model=List[GalleryResponse])
def get_gallery(db: Session = Depends(get_db)):
    return db.query(GalleryItemDB).order_by(GalleryItemDB.id.desc()).all()


@app.post("/api/gallery", response_model=GalleryResponse, dependencies=[Depends(verify_admin)])
def create_gallery_item(
    title: str = Form(...),
    description: str = Form(""),
    media_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    file_url, media_type = upload_to_r2(media_file)
    new_item = GalleryItemDB(title=title, description=description, media_type=media_type, file_url=file_url)
    db.add(new_item)
    db.commit()
    db.refresh(new_item)
    return new_item


# --- Contact ---
@app.post("/api/contact", status_code=201)
def create_contact_message(payload: ContactCreate, db: Session = Depends(get_db)):
    new_message = ContactMessageDB(**payload.model_dump())
    db.add(new_message)
    db.commit()
    db.refresh(new_message)
    send_contact_email(payload.name, payload.email, payload.reason, payload.message)
    return {"success": True, "id": new_message.id}
