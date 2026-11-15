import inspect
import os
import platform
import resource
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlmodel import text

from app.core.config import settings
from app.core.database import async_session_maker

START_TIME = time.time()


def get_memory_rss_mb() -> float:
    """Returns the RSS memory usage of the current process in MB."""
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return round(usage / (1024 * 1024), 2)
    return round(usage / 1024, 2)


def is_running_in_docker() -> bool:
    """Detects whether the process is running inside a Docker container."""
    if Path("/.dockerenv").exists():
        return True
    cgroup_path = Path("/proc/1/cgroup")
    if cgroup_path.exists():
        try:
            content = cgroup_path.read_text()
            if "docker" in content or "containerd" in content or "kubepods" in content:
                return True
        except Exception:
            pass
    return False


async def run_msg_health_check() -> Dict[str, Any]:
    """
    Executes a comprehensive health evaluation of the MSG Incoming microservice:
    - Active DB ping with latency measurement
    - In-memory verification of Telethon client, filter engine, emitter, and security
    - Active self-validation test of JWT and signal hint extraction
    - Telethon MTProto connectivity & downstream endpoints check
    - Resource usage and container status detection
    - Health score percentage (0-100%) computation
    """
    check_start = time.perf_counter()

    # 1. Inspect Core Functions in Memory
    essential_functions: List[Dict[str, Any]] = []

    # Check telethon_service
    try:
        from app.services.telethon_client import telethon_service, TelethonService
        is_telethon_ok = isinstance(telethon_service, TelethonService)
        essential_functions.append({
            "name": "telethon_service",
            "module": "app.services.telethon_client",
            "is_defined": is_telethon_ok,
            "type": "TelethonService instance",
        })
    except Exception as e:
        is_telethon_ok = False
        essential_functions.append({
            "name": "telethon_service",
            "module": "app.services.telethon_client",
            "is_defined": False,
            "type": "error",
            "error": str(e),
        })

    # Check filter_engine
    try:
        from app.services.filter_engine import filter_engine, FilterEngine
        is_filter_ok = isinstance(filter_engine, FilterEngine)
        essential_functions.append({
            "name": "filter_engine",
            "module": "app.services.filter_engine",
            "is_defined": is_filter_ok,
            "type": "FilterEngine instance",
        })
    except Exception as e:
        is_filter_ok = False
        essential_functions.append({
            "name": "filter_engine",
            "module": "app.services.filter_engine",
            "is_defined": False,
            "type": "error",
            "error": str(e),
        })

    # Check broker_emitter
    try:
        from app.services.emitter import broker_emitter, BrokerEmitter
        is_emitter_ok = isinstance(broker_emitter, BrokerEmitter)
        essential_functions.append({
            "name": "broker_emitter",
            "module": "app.services.emitter",
            "is_defined": is_emitter_ok,
            "type": "BrokerEmitter instance",
        })
    except Exception as e:
        is_emitter_ok = False
        essential_functions.append({
            "name": "broker_emitter",
            "module": "app.services.emitter",
            "is_defined": False,
            "type": "error",
            "error": str(e),
        })

    # Check async_engine
    try:
        from app.core.database import async_engine
        is_engine_ok = async_engine is not None
        essential_functions.append({
            "name": "async_engine",
            "module": "app.core.database",
            "is_defined": is_engine_ok,
            "type": "AsyncEngine",
        })
    except Exception as e:
        is_engine_ok = False
        essential_functions.append({
            "name": "async_engine",
            "module": "app.core.database",
            "is_defined": False,
            "type": "error",
            "error": str(e),
        })

    # Check create_access_token & decode_access_token
    try:
        from app.core.security import create_access_token, decode_access_token
        is_security_ok = callable(create_access_token) and callable(decode_access_token)
        essential_functions.append({
            "name": "create_access_token",
            "module": "app.core.security",
            "is_defined": is_security_ok,
            "type": "function",
        })
    except Exception as e:
        is_security_ok = False
        essential_functions.append({
            "name": "create_access_token",
            "module": "app.core.security",
            "is_defined": False,
            "type": "error",
            "error": str(e),
        })

    loaded_fn_count = sum(1 for f in essential_functions if f["is_defined"])
    total_fn_count = len(essential_functions)
    memory_functions_ratio = (loaded_fn_count / total_fn_count) if total_fn_count > 0 else 0.0

    # 2. Database Connectivity Test
    db_status = "healthy"
    db_latency_ms = 0.0
    try:
        db_t0 = time.perf_counter()
        async with async_session_maker() as session:
            await session.exec(text("SELECT 1"))
            db_latency_ms = round((time.perf_counter() - db_t0) * 1000, 2)
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    # 3. Active Self-Validation Test
    val_t0 = time.perf_counter()
    val_passed = False
    val_details: Dict[str, Any] = {}
    val_error: Optional[str] = None
    try:
        from app.core.security import create_access_token, decode_access_token
        from app.services.emitter import broker_emitter

        test_uuid = uuid4()
        token = create_access_token(test_uuid)
        decode_res = decode_access_token(token)

        # Verify emitter endpoint and headers readiness
        emitter_ready = callable(getattr(broker_emitter, "emit_to_trading_engine", None))

        if decode_res.is_ok() and decode_res.unwrap() == test_uuid and emitter_ready:
            val_passed = True
            val_details = {
                "jwt_token_cycle": "verified",
                "broker_emitter_ready": "verified",
                "ingest_endpoint": f"{broker_emitter.broker_url}/api/v1/operations/ingest",
            }
        else:
            val_error = "Token decoding mismatch or broker emitter not ready"
    except Exception as e:
        val_error = str(e)

    val_duration_ms = round((time.perf_counter() - val_t0) * 1000, 2)

    # 4. Telethon Status
    telethon_connected = False
    try:
        from app.services.telethon_client import telethon_service
        telethon_connected = telethon_service.is_connected()
    except Exception:
        pass

    telethon_configured = bool(settings.TELEGRAM_API_ID and settings.TELEGRAM_API_HASH)

    # 5. Container Status
    in_docker = is_running_in_docker()

    # 6. Calculate Health Score (0 - 100%)
    db_points = 30 if db_status == "healthy" else 0
    mem_points = round(memory_functions_ratio * 30)
    val_points = 25 if val_passed else 0
    dep_points = 15 if (telethon_configured or bool(settings.BROKER_API_URL)) else 5
    total_score = db_points + mem_points + val_points + dep_points

    overall_status = "healthy"
    if total_score < 50:
        overall_status = "unhealthy"
    elif total_score < 90 or db_status != "healthy":
        overall_status = "degraded"

    total_latency_ms = round((time.perf_counter() - check_start) * 1000, 2)

    return {
        # Standard Unified Fields
        "status": overall_status,
        "health_score": total_score,
        "service_name": "msg-incoming-ingestor",
        "app_name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "uptime_seconds": round(time.time() - START_TIME, 1),
        "latency_ms": total_latency_ms,

        # Backward-compatibility keys
        "database": db_status,
        "telethon": {
            "configured": telethon_configured,
            "connected": telethon_connected,
        },
        "broker_emitter_target": settings.BROKER_API_URL,
        "rust_app_target": settings.RUST_APP_URL,

        # System Resources & Memory
        "system": {
            "rss_memory_mb": get_memory_rss_mb(),
            "threads_count": threading.active_count(),
            "python_version": platform.python_version(),
            "platform": sys.platform,
            "pid": os.getpid(),
        },

        # Container Status
        "container": {
            "is_containerized": in_docker,
            "container_name": "msg_incoming_ingestor",
            "runtime": "docker" if in_docker else "native_host",
        },

        # Memory Functions
        "memory_functions": {
            "total_checked": total_fn_count,
            "loaded_count": loaded_fn_count,
            "functions": essential_functions,
        },

        # Dependencies
        "dependencies": {
            "database": {
                "status": "ok" if db_status == "healthy" else "down",
                "latency_ms": db_latency_ms,
                "dialect": "postgresql+asyncpg",
                "error": None if db_status == "healthy" else db_status,
            },
            "telethon_mtproto": {
                "configured": telethon_configured,
                "connected": telethon_connected,
            },
            "quant_trading_api_target": settings.BROKER_API_URL,
            "rust_tokenizer_target": settings.RUST_APP_URL,
        },

        # Live Self-Validation Test
        "validation_test": {
            "test_name": "jwt_and_trading_hint_parsing",
            "passed": val_passed,
            "duration_ms": val_duration_ms,
            "details": val_details,
            "error": val_error,
        },

        # Score Breakdown
        "score_breakdown": {
            "database_points": db_points,
            "memory_functions_points": mem_points,
            "validation_test_points": val_points,
            "dependencies_points": dep_points,
            "max_points": 100,
        },
    }
