from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.logging_config import configure_json_logging
from app.middleware import CorrelationIdMiddleware

configure_json_logging()

app = FastAPI(title="UploadService")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)
app.add_middleware(CorrelationIdMiddleware)

@app.get("/")
def home():
    return {"message": "FastAPI is running!"}

@app.get("/api/hello")
def hello():
    return {
        "message": "Hello from FastAPI!",
        "status": "success"
    }


from app.routes.uploads import router as uploads_router
from app.routes.files import router as files_router

app.include_router(uploads_router)
app.include_router(files_router)
