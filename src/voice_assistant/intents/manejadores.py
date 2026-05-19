"""
Manejadores de intenciones: lógica de ejecución tras emparejar el ``id`` del catálogo.

Reglas:
  - ``data/catalogo_intenciones.json`` solo define **qué intención** detectar.
  - Cada ``id`` debe tener aquí una función registrada en ``REGISTRO_MANEJADORES``.

Para añadir una intención nueva:
  1. Entrada en el JSON (``id``, ``disparadores``, ``prioridad``, …).
  2. Función ``manejar_<id>`` en este archivo.
  3. Registrarla en ``REGISTRO_MANEJADORES``.
"""

from __future__ import annotations

from collections.abc import Callable

from .ejecutor import reproducir_audio

# Rutas de audio de respuesta (relativas a la raíz del repositorio).
_AUDIO_SALUDO = "audio_messages/saludo.wav"
_AUDIO_NUEVA_REUNION = "audio_messages/new_reunion.wav"


def manejar_saludar(*, bloqueante: bool = True) -> None:
    """Intención ``saludar``: reproduce el saludo por audio."""
    reproducir_audio(_AUDIO_SALUDO, bloqueante=bloqueante)


def manejar_nueva_reunion(*, bloqueante: bool = True) -> None:
    """Intención ``nueva_reunion``: mensaje en consola y audio de confirmación."""
    print("Creando nueva reunion...")
    reproducir_audio(_AUDIO_NUEVA_REUNION, bloqueante=bloqueante)


ManejadorIntencion = Callable[..., None]

REGISTRO_MANEJADORES: dict[str, ManejadorIntencion] = {
    "saludar": manejar_saludar,
    "nueva_reunion": manejar_nueva_reunion,
}


def ejecutar_intencion(intencion_id: str, *, bloqueante: bool = True) -> None:
    """
    Despacha al manejador registrado para ``intencion_id``.

    Raises:
        ValueError: si no hay manejador para ese id (falta implementar o registrar).
    """
    manejador = REGISTRO_MANEJADORES.get(intencion_id)
    if manejador is None:
        raise ValueError(
            f"No hay manejador registrado para la intención {intencion_id!r}. "
            f"Añádalo en intents/manejadores.py (ids conocidos: {sorted(REGISTRO_MANEJADORES)})."
        )
    manejador(bloqueante=bloqueante)
