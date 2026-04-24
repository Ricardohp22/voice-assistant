"""Submódulo de entrada/salida de audio."""

from .capture import (
    grabar_muestras,
    guardar_wav_mono,
    listar_dispositivos_entrada,
)

__all__ = [
    "grabar_muestras",
    "guardar_wav_mono",
    "listar_dispositivos_entrada",
]
