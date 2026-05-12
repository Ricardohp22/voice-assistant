"""Ejecución de acciones declaradas en el catálogo de intenciones."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sounddevice as sd

from voice_assistant.audio.wav_io import cargar_wav_pcm16_mono_float32

from .catalogo import raiz_repositorio


def _resolver_ruta_audio(ruta: str) -> Path:
    p = Path(ruta)
    if p.is_absolute():
        return p
    return raiz_repositorio() / p


def ejecutar_accion(accion: dict[str, Any], *, bloqueante: bool = False) -> None:
    """
    Ejecuta ``accion`` (campos ``tipo`` y ``parametros``).

    Raises:
        ValueError: tipo no soportado o parámetros inválidos.
    """
    tipo = str(accion.get("tipo", "")).strip()
    params = accion.get("parametros") or {}
    if not isinstance(params, dict):
        raise ValueError("parametros debe ser un objeto")

    if tipo == "reproducir_audio":
        ruta = str(params.get("ruta", "")).strip()
        if not ruta:
            raise ValueError("reproducir_audio requiere parametros.ruta")
        archivo = _resolver_ruta_audio(ruta)
        if not archivo.is_file():
            raise FileNotFoundError(f"No existe el audio: {archivo}")
        audio, sr = cargar_wav_pcm16_mono_float32(archivo)
        sd.play(audio, samplerate=sr, blocking=bloqueante)
        return

    raise ValueError(f"Tipo de acción no soportado: {tipo!r}")
