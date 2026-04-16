"""Frontend WebSocket endpoint."""
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.errors import AppError
from app.logging_config import log
from app.models.user import User
from app.services import auth_service
from app.services.ws_manager import manager

router = APIRouter()


@router.websocket("/ws")
async def ws_endpoint(websocket: WebSocket, token: str = Query(...)):
    # Authenticate via JWT in query string
    try:
        user_id = auth_service.decode_access_token(token)
    except AppError:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id, User.is_active.is_(True)))
        user = result.scalar_one_or_none()
    if user is None:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    await manager.register(user.id, websocket)
    await websocket.send_json({"type": "connected", "user_id": str(user.id)})

    try:
        while True:
            # Accept pings / client messages; echo-minimal protocol for now
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error("ws_session_error", user_id=str(user.id), error=str(e))
    finally:
        await manager.unregister(user.id, websocket)
