from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="UploadService")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],)

@app.get("/")
def home():
    return {"message": "FastAPI is running!"}

@app.get("/api/hello")
def hello():
    return {
        "message": "Hello from FastAPI!",
        "status": "success"
    }
