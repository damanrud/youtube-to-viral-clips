from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from app.api.v1 import auth, processing, credits
from app.utils.websocket_manager import manager

app = FastAPI(title="Viral Shorts Generator API", version="1.0")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React dev server
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Роутеры
app.include_router(auth.router, prefix="/api/v1")
app.include_router(processing.router, prefix="/api/v1")
app.include_router(credits.router, prefix="/api/v1")

# WebSocket
@app.websocket("/ws/{job_id}")
async def websocket_endpoint(websocket: WebSocket, job_id: str):
    await manager.connect(job_id, websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive
    except WebSocketDisconnect:
        manager.disconnect(job_id, websocket)

@app.get("/")
def root():
    return {"message": "Viral Shorts Generator API"}