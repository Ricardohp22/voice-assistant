"""
Publicación de solicitudes de nueva reunión hacia Redis (consumo desde Node.js).

Contrato pensado para reacción inmediata vía Pub/Sub y lectura de respaldo vía clave.
Ver ``docs/redis_reunion_node.md``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from voice_assistant import config


def _cliente_redis():
    import redis

    return redis.Redis(
        host=config.REDIS_HOST,
        port=config.REDIS_PORT,
        db=config.REDIS_DB,
        password=config.REDIS_PASSWORD or None,
        decode_responses=True,
        socket_connect_timeout=config.REDIS_SOCKET_TIMEOUT_SEG,
    )


def _payload_iniciar_reunion(nombre_reunion: str, transcripcion: str | None) -> dict[str, Any]:
    solicitud_id = str(uuid.uuid4())
    ahora = datetime.now(timezone.utc).isoformat()
    return {
        "evento": "iniciar_reunion",
        "solicitud_id": solicitud_id,
        "nombre_reunion": nombre_reunion,
        "transcripcion": transcripcion if transcripcion is not None else nombre_reunion,
        "timestamp": ahora,
        "origen": "voice-assistant",
    }


def publicar_solicitud_iniciar_reunion(
    nombre_reunion: str,
    *,
    transcripcion: str | None = None,
) -> str:
    """
    Notifica a Node.js que debe crear una reunión con ``nombre_reunion``.

    1. ``SET`` en ``REDIS_CLAVE_ULTIMA_SOLICITUD`` (JSON) con TTL opcional.
    2. ``PUBLISH`` en ``REDIS_CANAL_REUNION_EVENTOS`` con el mismo JSON (reacción inmediata).

    Returns:
        ``solicitud_id`` del payload publicado.

    Raises:
        redis.RedisError: si Redis no está disponible o falla la escritura.
        ValueError: si ``nombre_reunion`` está vacío.
    """
    nombre = nombre_reunion.strip()
    if not nombre:
        raise ValueError("nombre_reunion no puede estar vacío")

    if not config.REDIS_HABILITADO:
        raise RuntimeError("Redis deshabilitado en config (REDIS_HABILITADO=False)")

    payload = _payload_iniciar_reunion(nombre, transcripcion)
    cuerpo = json.dumps(payload, ensure_ascii=False)
    solicitud_id = str(payload["solicitud_id"])

    cliente = _cliente_redis()
    cliente.ping()

    # Clave de respaldo: Node puede GET si arranca tarde o perdió un mensaje Pub/Sub.
    if config.REDIS_SOLICITUD_TTL_SEG > 0:
        cliente.setex(
            config.REDIS_CLAVE_ULTIMA_SOLICITUD,
            int(config.REDIS_SOLICITUD_TTL_SEG),
            cuerpo,
        )
    else:
        cliente.set(config.REDIS_CLAVE_ULTIMA_SOLICITUD, cuerpo)

    # Notificación push para suscriptores (flujo Node en la misma Pi).
    cliente.publish(config.REDIS_CANAL_REUNION_EVENTOS, cuerpo)

    return solicitud_id
