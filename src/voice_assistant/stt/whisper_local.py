"""STT local con faster-whisper (float32 mono, 16 kHz). Usado por ``--wake-turn``."""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from faster_whisper import WhisperModel

# Reutilizar el mismo modelo entre turnos evita recargar pesos en cada ``--wake-turn``.
_cache_modelo: tuple[str, str, str, "WhisperModel | None"] = ("", "", "", None)


def _obtener_modelo(nombre: str, dispositivo: str, tipo_computo: str) -> "WhisperModel":
    """
    Devuelve una instancia cacheada de ``WhisperModel``.

    La clave de caché es (nombre, dispositivo, tipo_computo). Cambiar cualquiera
    en ``config.WHISPER_*`` fuerza una nueva carga en el siguiente turno.
    """
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
    Transcribe audio mono float32 en [-1, 1] ya a 16 kHz (salida de ``preparar_muestras_para_stt``).

    Parámetros ``modelo``, ``dispositivo``, ``tipo_computo`` e ``idioma`` suelen
    venir de ``config.WHISPER_*``. ``beam_size=1`` prioriza velocidad en Raspberry;
    ``vad_filter=False`` porque el turno ya delimita la ventana de grabación.

    Raises:
        ImportError: si faster-whisper no está instalado.
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
    # Unir segmentos de Whisper en una sola cadena para el emparejador de intenciones.
    partes = [s.text.strip() for s in segmentos]
    return " ".join(p for p in partes if p).strip()
