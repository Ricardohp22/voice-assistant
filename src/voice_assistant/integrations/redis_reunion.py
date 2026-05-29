"""
Comunicación bidireccional Python ↔ Node.js vía Redis (flujo nueva reunión).

Canales (Pub/Sub):
  - ``REDIS_CANAL_COMANDOS``   — Python → Node  (órdenes)
  - ``REDIS_CANAL_RESPUESTAS`` — Node → Python (estatus)

Claves de respaldo (GET):
  - ``REDIS_CLAVE_ULTIMA_SOLICITUD`` — último comando publicado
  - ``REDIS_CLAVE_RESPUESTA_PREFIJO`` + ``solicitud_id`` — respuesta por solicitud

Ver ``docs/redis_reunion_node.md``.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from voice_assistant import config

# --- Eventos y estados del contrato ---
EVENTO_COMANDO_INICIAR = "iniciar_reunion"
EVENTO_RESPUESTA_INICIAR = "respuesta_iniciar_reunion"

EstadoReunion = Literal["exito", "error", "error_sesion", "error_reunion", "error_conexion", "error_timeout"]
ESTADOS_ERROR: frozenset[str] = frozenset(
    {"error", "error_sesion", "error_reunion", "error_conexion", "error_timeout"}
)


@dataclass(frozen=True)
class ResultadoRespuestaReunion:
    """Respuesta de Node tras intentar crear la reunión."""

    solicitud_id: str
    estado: str
    mensaje: str
    detalle: dict[str, Any]
    origen: str

    @property
    def exito(self) -> bool:
        return self.estado == "exito"


@dataclass(frozen=True)
class ResultadoSolicitudReunion:
    """Resultado de publicar comando y esperar respuesta (o timeout)."""

    solicitud_id: str
    respuesta: ResultadoRespuestaReunion | None
    """``None`` si no hubo respuesta en el tiempo configurado."""

    @property
    def exito(self) -> bool:
        return self.respuesta is not None and self.respuesta.exito


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


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clave_respuesta(solicitud_id: str) -> str:
    return f"{config.REDIS_CLAVE_RESPUESTA_PREFIJO}{solicitud_id}"


def _payload_comando_iniciar(nombre_reunion: str, transcripcion: str | None) -> dict[str, Any]:
    solicitud_id = str(uuid.uuid4())
    return {
        "evento": EVENTO_COMANDO_INICIAR,
        "solicitud_id": solicitud_id,
        "nombre_reunion": nombre_reunion,
        "transcripcion": transcripcion if transcripcion is not None else nombre_reunion,
        "timestamp": _ahora_iso(),
        "origen": "voice-assistant",
    }


def _parsear_respuesta(data: dict[str, Any]) -> ResultadoRespuestaReunion | None:
    if data.get("evento") != EVENTO_RESPUESTA_INICIAR:
        return None
    solicitud_id = str(data.get("solicitud_id", "")).strip()
    estado = str(data.get("estado", "")).strip().lower()
    if not solicitud_id or not estado:
        return None
    detalle = data.get("detalle")
    return ResultadoRespuestaReunion(
        solicitud_id=solicitud_id,
        estado=estado,
        mensaje=str(data.get("mensaje", "")).strip(),
        detalle=detalle if isinstance(detalle, dict) else {},
        origen=str(data.get("origen", "node")).strip(),
    )


def _leer_respuesta_desde_json(cuerpo: str, solicitud_id: str) -> ResultadoRespuestaReunion | None:
    try:
        data = json.loads(cuerpo)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("solicitud_id", "")) != solicitud_id:
        return None
    return _parsear_respuesta(data)


def esperar_respuesta_iniciar_reunion(
    solicitud_id: str,
    *,
    timeout_seg: float | None = None,
    pubsub: object | None = None,
) -> ResultadoRespuestaReunion | None:
    """
    Espera en ``REDIS_CANAL_RESPUESTAS`` (y clave por ``solicitud_id``) la respuesta de Node.

    Args:
        solicitud_id: UUID devuelto al publicar el comando.
        timeout_seg: Máximo de espera; por defecto ``REDIS_RESPUESTA_TIMEOUT_SEG``.
        pubsub: Si se pasó uno ya suscrito al canal de respuestas, se reutiliza.

    Returns:
        ``ResultadoRespuestaReunion`` o ``None`` si vence el tiempo.
    """
    limite = timeout_seg if timeout_seg is not None else config.REDIS_RESPUESTA_TIMEOUT_SEG
    cliente = _cliente_redis()
    clave = _clave_respuesta(solicitud_id)
    cerrar_pubsub = False

    if pubsub is None:
        pubsub = cliente.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(config.REDIS_CANAL_RESPUESTAS)
        cerrar_pubsub = True

    deadline = time.monotonic() + limite
    try:
        while time.monotonic() < deadline:
            restante = max(0.05, deadline - time.monotonic())
            msg = pubsub.get_message(timeout=min(0.25, restante))
            if msg and msg.get("type") == "message":
                cuerpo = msg.get("data")
                if isinstance(cuerpo, bytes):
                    cuerpo = cuerpo.decode("utf-8")
                if isinstance(cuerpo, str):
                    hit = _leer_respuesta_desde_json(cuerpo, solicitud_id)
                    if hit is not None:
                        return hit

            cuerpo_clave = cliente.get(clave)
            if cuerpo_clave:
                hit = _leer_respuesta_desde_json(cuerpo_clave, solicitud_id)
                if hit is not None:
                    return hit
    finally:
        if cerrar_pubsub:
            try:
                pubsub.unsubscribe(config.REDIS_CANAL_RESPUESTAS)
                pubsub.close()
            except Exception:
                pass

    return None


def publicar_solicitud_iniciar_reunion(
    nombre_reunion: str,
    *,
    transcripcion: str | None = None,
) -> str:
    """
    Publica comando ``iniciar_reunion`` hacia Node (sin esperar respuesta).

    Returns:
        ``solicitud_id`` del payload publicado.
    """
    nombre = nombre_reunion.strip()
    if not nombre:
        raise ValueError("nombre_reunion no puede estar vacío")
    if not config.REDIS_HABILITADO:
        raise RuntimeError("Redis deshabilitado en config (REDIS_HABILITADO=False)")

    payload = _payload_comando_iniciar(nombre, transcripcion)
    cuerpo = json.dumps(payload, ensure_ascii=False)
    solicitud_id = str(payload["solicitud_id"])

    cliente = _cliente_redis()
    cliente.ping()

    if config.REDIS_SOLICITUD_TTL_SEG > 0:
        cliente.setex(config.REDIS_CLAVE_ULTIMA_SOLICITUD, int(config.REDIS_SOLICITUD_TTL_SEG), cuerpo)
    else:
        cliente.set(config.REDIS_CLAVE_ULTIMA_SOLICITUD, cuerpo)

    cliente.publish(config.REDIS_CANAL_COMANDOS, cuerpo)
    return solicitud_id


def publicar_y_esperar_respuesta_iniciar_reunion(
    nombre_reunion: str,
    *,
    transcripcion: str | None = None,
    timeout_seg: float | None = None,
) -> ResultadoSolicitudReunion:
    """
    Publica ``iniciar_reunion`` y espera la respuesta de Node (máx. 5 s por defecto).

    Suscripción al canal de respuestas **antes** de publicar el comando para no perder
    respuestas rápidas.
    """
    nombre = nombre_reunion.strip()
    if not nombre:
        raise ValueError("nombre_reunion no puede estar vacío")
    if not config.REDIS_HABILITADO:
        raise RuntimeError("Redis deshabilitado en config (REDIS_HABILITADO=False)")

    payload = _payload_comando_iniciar(nombre, transcripcion)
    cuerpo = json.dumps(payload, ensure_ascii=False)
    solicitud_id = str(payload["solicitud_id"])
    limite = timeout_seg if timeout_seg is not None else config.REDIS_RESPUESTA_TIMEOUT_SEG

    cliente = _cliente_redis()
    cliente.ping()
    pubsub = cliente.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(config.REDIS_CANAL_RESPUESTAS)

    try:
        if config.REDIS_SOLICITUD_TTL_SEG > 0:
            cliente.setex(config.REDIS_CLAVE_ULTIMA_SOLICITUD, int(config.REDIS_SOLICITUD_TTL_SEG), cuerpo)
        else:
            cliente.set(config.REDIS_CLAVE_ULTIMA_SOLICITUD, cuerpo)

        cliente.publish(config.REDIS_CANAL_COMANDOS, cuerpo)

        respuesta = esperar_respuesta_iniciar_reunion(
            solicitud_id,
            timeout_seg=limite,
            pubsub=pubsub,
        )
    finally:
        try:
            pubsub.unsubscribe(config.REDIS_CANAL_RESPUESTAS)
            pubsub.close()
        except Exception:
            pass

    return ResultadoSolicitudReunion(solicitud_id=solicitud_id, respuesta=respuesta)
