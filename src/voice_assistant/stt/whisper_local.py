"""STT local con faster-whisper (float32 mono, 16 kHz)."""

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
    Transcribe audio mono float32 en [-1, 1] a 16 kHz.

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
    partes = [s.text.strip() for s in segmentos]
    return " ".join(p for p in partes if p).strip()
