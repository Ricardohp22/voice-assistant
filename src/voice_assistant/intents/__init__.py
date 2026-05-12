"""Enrutamiento local de intenciones tras el wakeup (catálogo JSON)."""

from .catalogo import (
    ResultadoEmpareo,
    cargar_catalogo,
    emparejar_intencion,
    raiz_repositorio,
)
from .ejecutor import ejecutar_accion

__all__ = [
    "ResultadoEmpareo",
    "cargar_catalogo",
    "emparejar_intencion",
    "ejecutar_accion",
    "raiz_repositorio",
]
