"""Enrutamiento local de intenciones tras el wakeup (catálogo JSON + manejadores en código)."""

from .catalogo import (
    ResultadoEmpareo,
    cargar_catalogo,
    emparejar_intencion,
    raiz_repositorio,
)
from .manejadores import ejecutar_intencion, transcripcion_seguimiento_nueva_reunion

__all__ = [
    "ResultadoEmpareo",
    "cargar_catalogo",
    "emparejar_intencion",
    "ejecutar_intencion",
    "raiz_repositorio",
    "transcripcion_seguimiento_nueva_reunion",
]
