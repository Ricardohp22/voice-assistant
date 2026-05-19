"""Detección de wake word (iteración 5 en adelante)."""

from .openwakeword_stream import (
    asegurar_modelos_openwakeword,
    ejecutar_escucha_openwakeword,
    esperar_primera_activacion_wake,
)

__all__ = [
    "asegurar_modelos_openwakeword",
    "ejecutar_escucha_openwakeword",
    "esperar_primera_activacion_wake",
]
