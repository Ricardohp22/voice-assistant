"""Submódulo de entrada/salida de audio."""

from .capture import (
    grabar_muestras,
    guardar_wav_mono,
    listar_dispositivos_entrada,
    resolver_tasa_muestreo_entrada,
)
from .captura_continua import ejecutar_escucha_continua
from .dispositivo import (
    comprobar_entrada_entrega_muestras,
    describir_dispositivo_entrada,
    resolver_dispositivo_entrada,
)
from .formato_pipeline import (
    mono_float32,
    preparar_muestras_para_stt,
    remuestrear_mono_lineal,
)

__all__ = [
    "comprobar_entrada_entrega_muestras",
    "describir_dispositivo_entrada",
    "ejecutar_escucha_continua",
    "grabar_muestras",
    "guardar_wav_mono",
    "listar_dispositivos_entrada",
    "mono_float32",
    "preparar_muestras_para_stt",
    "remuestrear_mono_lineal",
    "resolver_dispositivo_entrada",
    "resolver_tasa_muestreo_entrada",
]
