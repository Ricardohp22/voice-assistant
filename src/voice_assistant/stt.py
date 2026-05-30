"""
Transcripción de voz a texto con faster-whisper (STT local).

Recibe audio float32 mono ya normalizado a 16 kHz — exactamente la salida de
``audio.preparar_muestras_para_stt`` — y devuelve el texto como cadena.

Consumidores:
  - pipeline.py  → transcribe la orden del usuario tras el wake.
  - intents.py   → transcribe el nombre de la reunión (segunda escucha).

El modelo se cachea en ``_cache_modelo`` para no recargar pesos en cada turno.
La clave de caché es (nombre, dispositivo, tipo_computo); cambiar cualquiera de
esos valores en config.WHISPER_* fuerza una nueva carga en el siguiente turno.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

_cache_modelo: tuple[str, str, str, "WhisperModel | None"] = ("", "", "", None)


def _obtener_modelo(nombre: str, dispositivo: str, tipo_computo: str) -> "WhisperModel":
    global _cache_modelo
    clave = (nombre, dispositivo, tipo_computo)
    if _cache_modelo[:3] == clave and _cache_modelo[3] is not None:
        return _cache_modelo[3]
    from faster_whisper import WhisperModel

    modelo = WhisperModel(nombre, device=dispositivo, compute_type=tipo_computo)
    _cache_modelo = (nombre, dispositivo, tipo_computo, modelo)
    return modelo


def transcribir_float32_16khz(
    audio_mono: np.ndarray,
    *,
    modelo: str,
    dispositivo: str,
    tipo_computo: str,
    idioma: str,
) -> str:
    """
    Transcribe audio mono float32 en [-1, 1] ya a 16 kHz.

    Parámetros de inferencia fijados para Raspberry Pi:
      - ``beam_size=1``      → prioriza velocidad sobre precisión.
      - ``vad_filter=False`` → el turno ya delimita la ventana; no hace falta VAD.

    Args:
        audio_mono: Array 1D float32 a 16 kHz (salida de ``preparar_muestras_para_stt``).
        modelo: Nombre del modelo Whisper (p. ej. "tiny"). Ver config.WHISPER_MODELO.
        dispositivo: "cpu" o "cuda". Ver config.WHISPER_DISPOSITIVO.
        tipo_computo: "int8", "float16", etc. Ver config.WHISPER_TIPO_COMPUTO.
        idioma: Código de idioma ISO 639-1 (p. ej. "es"). Ver config.WHISPER_IDIOMA.

    Returns:
        Texto transcrito como cadena (vacío si el audio no tiene contenido).

    Raises:
        ImportError: Si faster-whisper no está instalado.
    """
    x = np.asarray(audio_mono, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return ""

    m = _obtener_modelo(modelo, dispositivo, tipo_computo)
    segmentos, _info = m.transcribe(
        x,
        language=idioma if idioma else None,
        beam_size=1,
        vad_filter=False,
    )
    # Los segmentos de Whisper se unen en una sola cadena para el emparejador
    # de intenciones (intents.py → emparejar_intencion).
    partes = [s.text.strip() for s in segmentos]
    return " ".join(p for p in partes if p).strip()
