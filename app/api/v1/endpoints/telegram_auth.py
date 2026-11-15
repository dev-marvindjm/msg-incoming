import asyncio
from datetime import datetime, timezone
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from telethon.errors import (
    FloodWaitError,
    PhoneNumberInvalidError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    SessionPasswordNeededError,
)

from app.services.telethon_client import telethon_service

logger = logging.getLogger("msg_ingestor.telegram_auth")
router = APIRouter(prefix="/auth/telegram", tags=["Telegram MTProto Auth"])

# In-memory storage for pending hashes and QR login state
pending_phone_hashes = {}
active_qr_logins = {}


class SendCodeRequest(BaseModel):
    phone_number: str


class VerifyCodeRequest(BaseModel):
    phone_number: str
    code: str
    phone_code_hash: Optional[str] = None


class Verify2FARequest(BaseModel):
    phone_number: str
    password: str


class StringSessionRequest(BaseModel):
    session_string: str


@router.get("/status")
async def get_telegram_status():
    client = telethon_service.client
    connected = telethon_service.is_connected()
    authorized = False
    user_info = None

    if client and connected:
        try:
            authorized = await client.is_user_authorized()
            if authorized:
                me = await client.get_me()
                user_info = {
                    "id": str(me.id),
                    "username": f"@{me.username}" if me.username else "Usuario Telegram",
                    "first_name": me.first_name or "",
                    "phone": me.phone or "",
                }
        except Exception as e:
            logger.warning(f"Error checking telegram user authorization: {e}")

    return {
        "connected": connected,
        "authorized": authorized,
        "user": user_info,
    }


@router.post("/send-code")
async def send_telegram_code(payload: SendCodeRequest):
    client = await telethon_service.get_or_create_client()
    if not client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cliente Telethon no inicializado. Verifique TELEGRAM_API_ID y TELEGRAM_API_HASH.",
        )

    if not client.is_connected():
        await client.connect()

    try:
        clean_phone = payload.phone_number.strip().replace(" ", "").replace("-", "")
        sent_code = await client.send_code_request(clean_phone)
        pending_phone_hashes[clean_phone] = sent_code.phone_code_hash

        return {
            "success": True,
            "phone_code_hash": sent_code.phone_code_hash,
            "message": "Código de verificación enviado al chat oficial de Telegram.",
        }
    except PhoneNumberInvalidError:
        raise HTTPException(status_code=400, detail="El número de teléfono ingresado no es válido.")
    except FloodWaitError as e:
        raise HTTPException(status_code=429, detail=f"Demasiados intentos. Espera {e.seconds} segundos.")
    except Exception as e:
        logger.error(f"Error enviando código Telegram: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-code")
async def verify_telegram_code(payload: VerifyCodeRequest):
    client = await telethon_service.get_or_create_client()
    if not client:
        raise HTTPException(status_code=503, detail="Telethon client unavailable")

    if not client.is_connected():
        await client.connect()

    clean_phone = payload.phone_number.strip().replace(" ", "").replace("-", "")
    code_hash = payload.phone_code_hash or pending_phone_hashes.get(clean_phone)

    try:
        user = await client.sign_in(phone=clean_phone, code=payload.code.strip(), phone_code_hash=code_hash)
        me = user or await client.get_me()

        # Start event listeners
        await telethon_service.ensure_started()

        return {
            "success": True,
            "needs_2fa": False,
            "session": {
                "phone": clean_phone,
                "username": f"@{me.username}" if getattr(me, "username", None) else "@trader_quant",
                "firstName": getattr(me, "first_name", "") or "Operador Telegram",
                "userId": str(me.id),
                "authMethod": "phone",
                "connectedAt": datetime.now(timezone.utc).isoformat(),
                "pingMs": 24,
                "activeChannelsCount": 5,
            },
        }
    except SessionPasswordNeededError:
        return {"success": True, "needs_2fa": True}
    except PhoneCodeInvalidError:
        raise HTTPException(status_code=400, detail="Código de verificación incorrecto.")
    except PhoneCodeExpiredError:
        raise HTTPException(status_code=400, detail="El código de verificación ha expirado.")
    except Exception as e:
        logger.error(f"Error verificando código: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-2fa")
async def verify_telegram_2fa(payload: Verify2FARequest):
    client = await telethon_service.get_or_create_client()
    if not client:
        raise HTTPException(status_code=503, detail="Telethon client unavailable")

    try:
        user = await client.sign_in(password=payload.password)
        me = user or await client.get_me()
        await telethon_service.ensure_started()

        return {
            "success": True,
            "session": {
                "phone": payload.phone_number,
                "username": f"@{me.username}" if getattr(me, "username", None) else "@trader_2fa",
                "firstName": getattr(me, "first_name", "") or "Operador 2FA",
                "userId": str(me.id),
                "authMethod": "phone",
                "connectedAt": datetime.now(timezone.utc).isoformat(),
                "pingMs": 22,
                "activeChannelsCount": 5,
            },
        }
    except Exception as e:
        logger.error(f"Error en verify-2fa: {e}")
        raise HTTPException(status_code=400, detail=f"Contraseña 2FA incorrecta: {e}")


@router.get("/qr/start")
async def start_telegram_qr():
    client = await telethon_service.get_or_create_client()
    if not client:
        raise HTTPException(status_code=503, detail="Telethon client unavailable")

    if not client.is_connected():
        await client.connect()

    # Cancel any previous pending task
    prev_task = active_qr_logins.get("task")
    if prev_task and not prev_task.done():
        prev_task.cancel()

    try:
        qr_login = await client.qr_login()
    except Exception as e:
        logger.warning(f"Error during qr_login ({e}), reconnecting with clean persistent session...")
        telethon_service.client = telethon_service._build_client(force_file_session=True)
        client = telethon_service.client
        await client.connect()
        qr_login = await client.qr_login()

    # Run wait() in the background so it continuously listens for UpdateLoginToken
    qr_task = asyncio.create_task(qr_login.wait())

    active_qr_logins["qr_login"] = qr_login
    active_qr_logins["task"] = qr_task

    return {
        "success": True,
        "url": qr_login.url,  # Official format: tg://login?token=...
        "expires": qr_login.expires.isoformat() if hasattr(qr_login, "expires") and qr_login.expires else None,
    }


@router.get("/qr/check")
async def check_telegram_qr():
    qr_task: Optional[asyncio.Task] = active_qr_logins.get("task")
    if not qr_task:
        raise HTTPException(status_code=400, detail="No hay inicio de sesión QR pendiente.")

    if qr_task.done():
        try:
            user = qr_task.result()
            await telethon_service.ensure_started()
            return {
                "success": True,
                "session": {
                    "phone": getattr(user, "phone", "") or "Cuenta Telegram",
                    "username": f"@{user.username}" if getattr(user, "username", None) else "@trader_qr",
                    "firstName": getattr(user, "first_name", "") or "Usuario Telegram",
                    "userId": str(user.id),
                    "authMethod": "qr_code",
                    "connectedAt": datetime.now(timezone.utc).isoformat(),
                    "pingMs": 20,
                    "activeChannelsCount": 6,
                },
            }
        except SessionPasswordNeededError:
            return {"success": True, "needs_2fa": True}
        except asyncio.CancelledError:
            return {"success": False, "error": "Login QR cancelado."}
        except Exception as e:
            logger.error(f"Error in QR login wait: {e}")
            return {"success": False, "error": str(e)}

    return {"success": False, "waiting": True}
