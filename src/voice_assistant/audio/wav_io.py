"""Lectura mínima de WAV PCM 16-bit → float32 mono (reutilizable en wake, intenciones, etc.)."""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np


def cargar_wav_pcm16_mono_float32(ruta: str | Path) -> tuple[np.ndarray, int]:
    """
    Carga WAV PCM 16-bit; si hay varios canales, promedia a mono.

    Returns:
        (audio float32 en [-1, 1], tasa_hz)
    """
    p = Path(ruta)
    with wave.open(str(p), "rb") as wf:
        canales = int(wf.getnchannels())
        tasa = int(wf.getframerate())
        ancho = int(wf.getsampwidth())
        if ancho != 2:
            raise ValueError(f"Se esperaba PCM 16-bit en {p}, sampwidth={ancho}")
        data = wf.readframes(int(wf.getnframes()))
    arr = np.frombuffer(data, dtype=np.int16)
    if canales > 1:
        arr = arr.reshape(-1, canales).mean(axis=1).astype(np.int16, copy=False)
    audio = (arr.astype(np.float32) / 32767.0).reshape(-1)
    return np.clip(audio, -1.0, 1.0), tasa
