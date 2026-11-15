from fastapi import APIRouter
from app.core.health_checker import run_msg_health_check

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def api_v1_health():
    """
    Returns comprehensive health metrics for the MSG Incoming service.
    """
    return await run_msg_health_check()
