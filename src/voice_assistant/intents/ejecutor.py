"""Ejecución de acciones declaradas en el catálogo de intenciones (fase final de ``--wake-turn``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import sounddevice as sd

from voice_assistant.audio.wav_io import cargar_wav_pcm16_mono_float32

from .catalogo import raiz_repositorio


def _resolver_ruta_audio(ruta: str) -> Path:
    """Rutas del JSON son relativas a la raíz del repo salvo que sean absolutas."""
    p = Path(ruta)
    if p.is_absolute():
        return p
    return raiz_repositorio() / p


def ejecutar_accion(accion: dict[str, Any], *, bloqueante: bool = False) -> None:
    """
    Ejecuta la acción elegida tras ``emparejar_intencion``.

    El catálogo define ``tipo`` y ``parametros``. Hoy solo está implementado
    ``reproducir_audio`` (WAV de respuesta; opcional ``mensaje_consola`` antes).

    Args:
        accion: dict con al menos ``tipo`` y ``parametros``.
        bloqueante: si True, ``sd.play`` espera a que termine el audio (recomendado en CLI).

    Raises:
        ValueError: tipo no soportado o parámetros inválidos.
        FileNotFoundError: WAV de respuesta inexistente.
    """
    tipo = str(accion.get("tipo", "")).strip()
    params = accion.get("parametros") or {}
    if not isinstance(params, dict):
        raise ValueError("parametros debe ser un objeto")

    if tipo == "reproducir_audio":
        mensaje = params.get("mensaje_consola")
        if mensaje is not None:
            texto = str(mensaje).strip()
            if texto:
                print(texto)

        ruta = str(params.get("ruta", "")).strip()
        if not ruta:
            raise ValueError("reproducir_audio requiere parametros.ruta")
        archivo = _resolver_ruta_audio(ruta)
        if not archivo.is_file():
            print(f"Aviso: no existe el audio {archivo}; omitiendo reproducción.")
            return
        audio, sr = cargar_wav_pcm16_mono_float32(archivo)
        sd.play(audio, samplerate=sr, blocking=bloqueante)
        return

    raise ValueError(f"Tipo de acción no soportado: {tipo!r}")
