from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from pydantic import BaseModel
from typing import List

# 1. DATABASE CONFIGURATION
# Replace [YOUR-PASSWORD] with your actual Supabase DB password
DATABASE_URL = "postgresql://postgres.fskmdhvjwqasdnotbmvt:pcoss2026churchpass@aws-0-eu-central-1.pooler.supabase.com:6543/postgres"

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 2. DATABASE MODELS
class SermonDB(Base):
    __tablename__ = "sermons"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    preacher = Column(String, nullable=False)
    date = Column(String, nullable=False)

class ActivityDB(Base):
    __tablename__ = "activities"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)

Base.metadata.create_all(bind=engine)

# 3. FASTAPI SETUP
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://pcoss-church.pages.dev",
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://localhost:8000"
    ],
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

# 4. SCHEMAS
class SermonCreate(BaseModel):
    title: str
    preacher: str
    date: str

class SermonResponse(SermonCreate):
    id: int
    class Config:
        from_attributes = True

class ActivityCreate(BaseModel):
    title: str
    description: str

class ActivityResponse(ActivityCreate):
    id: int
    class Config:
        from_attributes = True

# 5. ENDPOINTS
@app.get("/")
def root():
    return {"message": "PCOSS Church API is running!"}

@app.post("/api/sermons", response_model=SermonResponse)
def create_sermon(sermon: SermonCreate, db: Session = Depends(get_db)):
    new_sermon = SermonDB(**sermon.model_dump())
    db.add(new_sermon)
    db.commit()
    db.refresh(new_sermon)
    return new_sermon

@app.get("/api/sermons", response_model=List[SermonResponse])
def get_sermons(db: Session = Depends(get_db)):
    return db.query(SermonDB).all()

@app.post("/api/activities", response_model=ActivityResponse)
def create_activity(activity: ActivityCreate, db: Session = Depends(get_db)):
    new_activity = ActivityDB(**activity.model_dump())
    db.add(new_activity)
    db.commit()
    db.refresh(new_activity)
    return new_activity

@app.get("/api/activities", response_model=List[ActivityResponse])
def get_activities(db: Session = Depends(get_db)):
    return db.query(ActivityDB).all()