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
from voice_assistant.audio.dispositivo import resolver_dispositivo_entrada
from voice_assistant.audio.formato_pipeline import preparar_muestras_para_stt
from voice_assistant.stt import transcribir_float32_16khz

from .ejecutor import reproducir_audio

# Rutas de audio de respuesta (relativas a la raíz del repositorio).
_AUDIO_SALUDO = "audio_messages/saludo.wav"
_AUDIO_ASK_NAME = "audio_messages/ask_name.wav"
_AUDIO_SUCCESFUL_MEETING = "audio_messages/new_reunion.wav"

# Última transcripción capturada en el flujo ``nueva_reunion`` (segunda escucha).
transcripcion_seguimiento_nueva_reunion: str | None = None


def _dispositivo_entrada() -> int | None:
    """Micrófono según ``config`` (misma resolución que ``main.py``)."""
    return resolver_dispositivo_entrada(
        config.MIC_NOMBRE_CONTIENE,
        config.DISPOSITIVO_ENTRADA,
    )


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


# Funcion a ejecutar cuando se detecta la intencion "Crear nueva reunion"
def manejar_nueva_reunion(*, bloqueante: bool = True) -> None:
    """
    Intención ``nueva_reunion``: mensaje, audio de confirmación y segunda escucha.

    Tras reproducir ``new_reunion.wav``, graba ``NUEVA_REUNION_ESCUCHA_SEG`` s y
    guarda la transcripción en ``transcripcion_seguimiento_nueva_reunion``.
    """
    global transcripcion_seguimiento_nueva_reunion

    ## Solicita el nombre de la reunion
    print("Cual es el nombre de la reunion...")
    # Siempre bloqueante antes del micrófono para no grabar encima del WAV de salida.
    reproducir_audio(_AUDIO_ASK_NAME, bloqueante=True)

    ## Captura el nombre de la reunion y lo transcribe
    transcripcion_seguimiento_nueva_reunion = _grabar_y_transcribir(config.NUEVA_REUNION_ESCUCHA_SEG)
    print(f"Nombre de la reunion: {transcripcion_seguimiento_nueva_reunion!r}")

    reproducir_audio(_AUDIO_SUCCESFUL_MEETING, bloqueante=True)
    


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
