from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import asyncio

app = FastAPI(title='InfoOS Core')

class ConnectionManager:
    """Manage active WebSocket connections for broadcasting task updates."""
    def __init__(self) -> None:
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: str) -> None:
        """Send a text message to all connected clients."""
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

@app.websocket("/ws/tasks/{task_id}")
async def websocket_endpoint(websocket: WebSocket, task_id: str):
    """WebSocket endpoint for streaming task state updates.

    Clients connect with a unique ``task_id``. The server currently echoes
    received messages back to all participants via the ``ConnectionManager``.
    """
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # For now, simply broadcast the received payload to all subscribers.
            await manager.broadcast(data)
    except WebSocketDisconnect:
        manager.disconnect(websocket)