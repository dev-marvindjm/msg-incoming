from fastapi import APIRouter

from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.messages import router as messages_router
from app.api.v1.endpoints.rules import router as rules_router

api_v1_router = APIRouter(prefix="/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(rules_router)
api_v1_router.include_router(messages_router)
