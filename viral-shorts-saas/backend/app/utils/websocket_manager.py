from typing import Dict, Set
from fastapi import WebSocket

class WebSocketManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, job_id: str, websocket: WebSocket):
        await websocket.accept()
        if job_id not in self.active_connections:
            self.active_connections[job_id] = set()
        self.active_connections[job_id].add(websocket)

    def disconnect(self, job_id: str, websocket: WebSocket):
        if job_id in self.active_connections:
            self.active_connections[job_id].discard(websocket)

    async def send(self, job_id: str, data: dict):
        if job_id in self.active_connections:
            for ws in self.active_connections[job_id]:
                await ws.send_json(data)

manager = WebSocketManager()