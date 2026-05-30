"""
Comunicación bidireccional Python ↔ Node.js vía Redis (flujo nueva reunión).

Contrato de mensajes (ver docs/redis_reunion_node.md):
  - Python publica en REDIS_CANAL_COMANDOS:
        { "evento": "iniciar_reunion", "solicitud_id": uuid, "nombre_reunion": ...,
          "transcripcion": ..., "timestamp": iso8601, "origen": "voice-assistant" }
  - Node responde en REDIS_CANAL_RESPUESTAS y/o con SET en la clave por solicitud_id:
        { "evento": "respuesta_iniciar_reunion", "solicitud_id": uuid,
          "estado": "exito"|"error*", "mensaje": ..., "detalle": {...} }

Mecanismo de doble canal (Pub/Sub + clave GET):
  - La suscripción al canal de respuestas se abre ANTES de publicar el comando,
    para no perder respuestas rápidas de Node (race condition).
  - Si el mensaje Pub/Sub no llega, se consulta la clave por solicitud_id como
    respaldo (útil si Node reinicia o hay lag en el broker).

El único punto de entrada desde intents.py es:
    ``publicar_y_esperar_respuesta_iniciar_reunion(nombre, transcripcion=...)``
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from voice_assistant import config

# Nombres de evento usados en el contrato JSON con Node.
EVENTO_COMANDO_INICIAR = "iniciar_reunion"
EVENTO_RESPUESTA_INICIAR = "respuesta_iniciar_reunion"

EstadoReunion = Literal["exito", "error", "error_sesion", "error_reunion", "error_conexion", "error_timeout"]
ESTADOS_ERROR: frozenset[str] = frozenset(
    {"error", "error_sesion", "error_reunion", "error_conexion", "error_timeout"}
)


# =============================================================================
# DATACLASSES DE RESPUESTA
# =============================================================================

@dataclass(frozen=True)
class ResultadoRespuestaReunion:
    """Respuesta de Node tras intentar crear la reunión (campo por campo del JSON)."""

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
    """Resultado de publicar el comando y esperar (o no) la respuesta de Node."""

    solicitud_id: str
    respuesta: ResultadoRespuestaReunion | None
    """None si no hubo respuesta dentro del timeout configurado."""

    @property
    def exito(self) -> bool:
        return self.respuesta is not None and self.respuesta.exito


# =============================================================================
# HELPERS INTERNOS
# =============================================================================

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
    """Clave Redis por solicitud: vox:reunion:respuesta:<uuid>"""
    return f"{config.REDIS_CLAVE_RESPUESTA_PREFIJO}{solicitud_id}"


def _payload_comando_iniciar(nombre_reunion: str, transcripcion: str | None) -> dict[str, Any]:
    """Construye el payload JSON del comando ``iniciar_reunion``."""
    return {
        "evento": EVENTO_COMANDO_INICIAR,
        "solicitud_id": str(uuid.uuid4()),
        "nombre_reunion": nombre_reunion,
        "transcripcion": transcripcion if transcripcion is not None else nombre_reunion,
        "timestamp": _ahora_iso(),
        "origen": "voice-assistant",
    }


def _parsear_respuesta(data: dict[str, Any]) -> ResultadoRespuestaReunion | None:
    """Valida y convierte el dict JSON de Node a ResultadoRespuestaReunion."""
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
    """Parsea cuerpo JSON y verifica que el solicitud_id coincide con el esperado."""
    try:
        data = json.loads(cuerpo)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if str(data.get("solicitud_id", "")) != solicitud_id:
        return None
    return _parsear_respuesta(data)


# =============================================================================
# ESPERA DE RESPUESTA
#
# Se suscribe al canal de respuestas (Pub/Sub) y en cada iteración también
# consulta la clave de respaldo por solicitud_id. El doble mecanismo cubre:
#   - Respuestas rápidas de Node: llegan por Pub/Sub.
#   - Latencia o reinicio de Node: aparecen en la clave GET.
# =============================================================================

def esperar_respuesta_iniciar_reunion(
    solicitud_id: str,
    *,
    timeout_seg: float | None = None,
    pubsub: object | None = None,
) -> ResultadoRespuestaReunion | None:
    """
    Escucha en REDIS_CANAL_RESPUESTAS y en la clave por ``solicitud_id``.

    Args:
        solicitud_id: UUID del comando publicado.
        timeout_seg: Máximo de espera; por defecto REDIS_RESPUESTA_TIMEOUT_SEG.
        pubsub: Objeto pubsub ya suscrito (optimización: evita re-suscribir si
                se llama desde ``publicar_y_esperar_respuesta_iniciar_reunion``).

    Returns:
        ResultadoRespuestaReunion o None si vence el tiempo.
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

            # Respaldo por clave GET: Node puede SET sin pasar por Pub/Sub.
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


# =============================================================================
# FUNCIÓN PÚBLICA PRINCIPAL
#
# Este es el único punto de entrada que usa intents.py. Combina publicar +
# esperar en una operación atómica desde el punto de vista del manejador.
# =============================================================================

def publicar_y_esperar_respuesta_iniciar_reunion(
    nombre_reunion: str,
    *,
    transcripcion: str | None = None,
    timeout_seg: float | None = None,
) -> ResultadoSolicitudReunion:
    """
    Publica el comando ``iniciar_reunion`` y espera la respuesta de Node.

    La suscripción al canal de respuestas se abre ANTES de publicar para no
    perder respuestas rápidas (evita la race condition: publish → suscribir).

    Args:
        nombre_reunion: Nombre capturado por STT en el segundo turno de escucha.
        transcripcion: Texto completo de la transcripción (opcional; si es None
                       se usa nombre_reunion como fallback).
        timeout_seg: Máximo de espera; por defecto REDIS_RESPUESTA_TIMEOUT_SEG.

    Returns:
        ResultadoSolicitudReunion con la respuesta de Node (o respuesta=None si timeout).

    Raises:
        ValueError: Si nombre_reunion está vacío.
        RuntimeError: Si Redis está deshabilitado en config.
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

    # Suscribir ANTES de publicar para no perder respuestas rápidas de Node.
    pubsub = cliente.pubsub(ignore_subscribe_messages=True)
    pubsub.subscribe(config.REDIS_CANAL_RESPUESTAS)

    try:
        # Guardar copia del comando (respaldo GET para Node si pierde el mensaje Pub/Sub).
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


def publicar_solicitud_iniciar_reunion(
    nombre_reunion: str,
    *,
    transcripcion: str | None = None,
) -> str:
    """
    Publica el comando ``iniciar_reunion`` sin esperar respuesta.

    Útil para disparar la acción de forma fire-and-forget. Devuelve el
    ``solicitud_id`` para que el llamador pueda hacer GET de la respuesta más tarde.

    Returns:
        solicitud_id (UUID string) del payload publicado.
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
