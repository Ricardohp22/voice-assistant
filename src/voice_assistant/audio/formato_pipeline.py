"""
Formato de salida unificado para la cadena voz → STT (p. ej. Whisper).

Contrato: **mono**, float32 en [-1, 1], tasa ``TASA_SALIDA_PIPELINE_HZ`` (16 kHz
por defecto). El guardado en WAV PCM 16-bit lo sigue haciendo ``guardar_wav_mono``.
"""

from __future__ import annotations

import numpy as np


def mono_float32(muestras: np.ndarray) -> np.ndarray:
    """
    Convierte (frames,) o (frames, C) float32 a un solo canal 1D.

    Varios canales se promedian; un canal se aplana.
    """
    audio = np.asarray(muestras, dtype=np.float32)
    if audio.ndim == 2:
        if audio.shape[1] > 1:
            audio = np.mean(audio, axis=1, dtype=np.float32)
        else:
            audio = audio[:, 0]
    elif audio.ndim != 1:
        raise ValueError("muestras debe ser 1D o 2D (frames, canales)")
    return np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False)


def remuestrear_mono_lineal(
    mono: np.ndarray,
    tasa_entrada_hz: int,
    tasa_salida_hz: int,
) -> np.ndarray:
    """
    Remuestreo por interpolación lineal (sin dependencias extra; suficiente para voz).

    Si las tasas coinciden, devuelve una copia vista del mismo contenido sin recalcular.
    """
    if tasa_entrada_hz <= 0 or tasa_salida_hz <= 0:
        raise ValueError("Las tasas de muestreo deben ser > 0")
    x = np.asarray(mono, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return x.copy()
    if tasa_entrada_hz == tasa_salida_hz:
        return x.astype(np.float32, copy=False)

    n_out = max(1, int(round(x.size * tasa_salida_hz / tasa_entrada_hz)))
    t_in = np.linspace(0.0, (x.size - 1) / tasa_entrada_hz, num=x.size, dtype=np.float64)
    t_out = np.linspace(0.0, (n_out - 1) / tasa_salida_hz, num=n_out, dtype=np.float64)
    y = np.interp(t_out, t_in, x.astype(np.float64))
    return np.clip(y, -1.0, 1.0).astype(np.float32)


def preparar_muestras_para_stt(
    muestras: np.ndarray,
    tasa_grabacion_hz: int,
    tasa_objetivo_hz: int,
) -> tuple[np.ndarray, int]:
    """
    Puente entre la grabación de la orden y Whisper (fase 4 de ``--wake-turn``).

    Convierte a mono float32 y remuestrea a ``tasa_objetivo_hz`` (típ. 16 kHz).
    Ese array es el que consume ``transcribir_float32_16khz``.

    Args:
        muestras: float32 tal como devuelve ``grabar_muestras``.
        tasa_grabacion_hz: tasa real de la grabación (p. ej. 48000 si el USB no acepta 16 k).
        tasa_objetivo_hz: tasa para STT (``config.TASA_SALIDA_PIPELINE_HZ``).

    Returns:
        ``(audio_mono_float32, tasa_objetivo_hz)``
    """
    mono = mono_float32(muestras)
    out = remuestrear_mono_lineal(mono, tasa_grabacion_hz, tasa_objetivo_hz)
    return out, int(tasa_objetivo_hz)
