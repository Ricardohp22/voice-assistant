"""
Manejadores de intenciones: lógica de ejecución tras emparejar el ``id`` del catálogo.

Reglas:
  - ``data/catalogo_intenciones.json`` solo define **qué intención** detectar.
  - Cada ``id`` debe tener aquí una función registrada en ``REGISTRO_MANEJADORES``.

Para añadir una intención nueva:
  1. Entrada en el JSON (``id``, ``disparadores``, ``prioridad``, …).
  2. Función ``manejar_<id>`` en este archivo.
  3. Registrarla en ``REGISTRO_MANEJADORES``.
"""

from __future__ import annotations

from collections.abc import Callable

from voice_assistant import config
from voice_assistant.audio.capture import grabar_muestras
from voice_assistant.audio.dispositivo import resolver_dispositivo_entrada_config
from voice_assistant.audio.formato_pipeline import preparar_muestras_para_stt
from voice_assistant.integrations import publicar_y_esperar_respuesta_iniciar_reunion
from voice_assistant.stt import transcribir_float32_16khz

from .ejecutor import reproducir_audio

# Rutas de audio de respuesta (relativas a la raíz del repositorio).
_AUDIO_SALUDO = "audio_messages/saludo.wav"
_AUDIO_ASK_NAME = "audio_messages/ask_name.wav"
_AUDIO_SUCCESFUL_MEETING = "audio_messages/new_reunion.wav"

# Última transcripción capturada en el flujo ``nueva_reunion`` (segunda escucha).
transcripcion_seguimiento_nueva_reunion: str | None = None
# Última respuesta Redis del flujo Node (reunión creada o error).
ultima_respuesta_reunion: object | None = None


def _dispositivo_entrada() -> int | None:
    """Micrófono según ``config`` (p. ej. ALSA ``compartido`` / dsnoop)."""
    return resolver_dispositivo_entrada_config()


def _grabar_y_transcribir(duracion_seg: float) -> str:
    """Graba ``duracion_seg`` s, normaliza a pipeline STT y devuelve el texto Whisper."""
    print(f"Escuchando {duracion_seg:.0f} s...")
    muestras, tasa_efectiva = grabar_muestras(
        duracion_seg,
        dispositivo=_dispositivo_entrada(),
        tasa_muestreo_hz=config.TASA_MUESTREO_HZ,
        canales=config.CANALES,
    )
    audio_16k, _ = preparar_muestras_para_stt(
        muestras,
        tasa_efectiva,
        config.TASA_SALIDA_PIPELINE_HZ,
    )
    return transcribir_float32_16khz(
        audio_16k,
        modelo=config.WHISPER_MODELO,
        dispositivo=config.WHISPER_DISPOSITIVO,
        tipo_computo=config.WHISPER_TIPO_COMPUTO,
        idioma=config.WHISPER_IDIOMA,
    )


def manejar_saludar(*, bloqueante: bool = True) -> None:
    """Intención ``saludar``: reproduce el saludo por audio."""
    reproducir_audio(_AUDIO_SALUDO, bloqueante=bloqueante)


def manejar_nueva_reunion(*, bloqueante: bool = True) -> None:
    """
    Intención ``nueva_reunion``: pide nombre, envía comando a Node vía Redis,
    espera estatus (máx. ``REDIS_RESPUESTA_TIMEOUT_SEG``) y solo si es ``exito``
    reproduce el audio de confirmación.
    """
    global transcripcion_seguimiento_nueva_reunion, ultima_respuesta_reunion

    print("Cual es el nombre de la reunion...")
    reproducir_audio(_AUDIO_ASK_NAME, bloqueante=True)

    transcripcion_seguimiento_nueva_reunion = _grabar_y_transcribir(config.NUEVA_REUNION_ESCUCHA_SEG)
    nombre_reunion = (transcripcion_seguimiento_nueva_reunion or "").strip()
    print(f"Nombre de la reunion: {nombre_reunion!r}")

    if not nombre_reunion:
        print("Aviso: no se capturó nombre; no se publica en Redis.")
        return

    ultima_respuesta_reunion = None
    try:
        print(
            f"Enviando crear reunión a Node (canal {config.REDIS_CANAL_COMANDOS!r}), "
            f"esperando respuesta hasta {config.REDIS_RESPUESTA_TIMEOUT_SEG:.0f} s..."
        )
        resultado = publicar_y_esperar_respuesta_iniciar_reunion(
            nombre_reunion,
            transcripcion=transcripcion_seguimiento_nueva_reunion,
        )
        ultima_respuesta_reunion = resultado.respuesta

        if resultado.respuesta is None:
            print(
                f"Sin respuesta de Node en {config.REDIS_RESPUESTA_TIMEOUT_SEG:.0f} s "
                f"(solicitud_id={resultado.solicitud_id}). No se reproduce audio de éxito."
            )
            return

        r = resultado.respuesta
        if r.exito:
            print(f"Reunión creada correctamente: {r.mensaje or '(sin mensaje)'}")
            reproducir_audio(_AUDIO_SUCCESFUL_MEETING, bloqueante=True)
        else:
            print(f"No se pudo crear la reunión [{r.estado}]: {r.mensaje or '(sin mensaje)'}")
            if r.detalle:
                print(f"Detalle: {r.detalle}")
    except Exception as exc:
        print(f"Error en comunicación Redis: {exc}")


ManejadorIntencion = Callable[..., None]

REGISTRO_MANEJADORES: dict[str, ManejadorIntencion] = {
    "saludar": manejar_saludar,
    "nueva_reunion": manejar_nueva_reunion,
}


def ejecutar_intencion(intencion_id: str, *, bloqueante: bool = True) -> None:
    """
    Despacha al manejador registrado para ``intencion_id``.

    Raises:
        ValueError: si no hay manejador para ese id (falta implementar o registrar).
    """
    manejador = REGISTRO_MANEJADORES.get(intencion_id)
    if manejador is None:
        raise ValueError(
            f"No hay manejador registrado para la intención {intencion_id!r}. "
            f"Añádalo en intents/manejadores.py (ids conocidos: {sorted(REGISTRO_MANEJADORES)})."
        )
    manejador(bloqueante=bloqueante)
