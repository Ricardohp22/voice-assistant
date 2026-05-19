"""Utilidades de reproducción de audio para manejadores de intenciones."""

from __future__ import annotations

from pathlib import Path

import sounddevice as sd

from voice_assistant.audio.wav_io import cargar_wav_pcm16_mono_float32

from .catalogo import raiz_repositorio


def _resolver_ruta_audio(ruta: str) -> Path:
    """Rutas relativas a la raíz del repo; absolutas se usan tal cual."""
    p = Path(ruta)
    if p.is_absolute():
        return p
    return raiz_repositorio() / p


def reproducir_audio(ruta: str, *, bloqueante: bool = False) -> None:
    """
    Reproduce un WAV mono por el dispositivo de salida por defecto.

    Si el archivo no existe, imprime aviso y no lanza excepción (útil en desarrollo).
    """
    archivo = _resolver_ruta_audio(ruta)
    if not archivo.is_file():
        print(f"Aviso: no existe el audio {archivo}; omitiendo reproducción.")
        return
    audio, sr = cargar_wav_pcm16_mono_float32(archivo)
    sd.play(audio, samplerate=sr, blocking=bloqueante)
