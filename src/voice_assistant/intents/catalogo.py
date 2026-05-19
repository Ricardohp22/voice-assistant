"""
Carga y emparejo local de intenciones frente a una oración (p. ej. salida de STT).

El catálogo vive en JSON (``data/catalogo_intenciones.json`` por defecto).
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def raiz_repositorio() -> Path:
    """Raíz del repo ``voice-assistant/`` (directorio que contiene ``src/`` y ``data/``)."""
    return Path(__file__).resolve().parents[3]


def normalizar_oracion(texto: str) -> str:
    """
    Prepara texto de STT para comparar con disparadores del catálogo.

    Minúsculas, sin acentos, espacios compactados. Así "Hola" y "hola" coinciden
    y variantes con tildes de Whisper no rompen el emparejo.
    """
    s = texto.strip().lower()
    nkfd = unicodedata.normalize("NFD", s)
    sin_acentos = "".join(c for c in nkfd if unicodedata.category(c) != "Mn")
    sin_acentos = re.sub(r"\s+", " ", sin_acentos).strip()
    return sin_acentos


def quitar_prefijos_wake(texto_norm: str, prefijos: list[str]) -> str:
    """
    Quita del inicio la variante de wake que Whisper suele transcribir.

    Los prefijos vienen de ``prefijos_wake`` en el JSON (p. ej. "hex vox device").
    Se prueban del más largo al más corto para no cortar de más.
    """
    t = texto_norm.strip()
    prefs_ord = sorted((normalizar_oracion(p) for p in prefijos), key=len, reverse=True)
    for p in prefs_ord:
        if t.startswith(p):
            resto = t[len(p) :].strip(" ,.;:-—")
            return resto if resto else t
    return t


@dataclass(frozen=True)
class ResultadoEmpareo:
    """Intención elegida y metadatos útiles para logs."""

    intencion_id: str
    intencion_titulo: str
    disparador: str
    texto_tras_wake: str
    accion: dict[str, Any]


def cargar_catalogo(ruta_relativa_o_absoluta: str | Path) -> dict[str, Any]:
    """
    Lee ``data/catalogo_intenciones.json`` (o ruta absoluta).

    Rutas relativas se resuelven desde la raíz del repositorio, no desde el cwd.
    """
    p = Path(ruta_relativa_o_absoluta)
    if not p.is_absolute():
        p = raiz_repositorio() / p
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def emparejar_intencion(catalogo: dict[str, Any], oracion: str) -> ResultadoEmpareo | None:
    """
    Elige la intención que mejor coincide con la transcripción del turno.

    Pasos:
      1. Normalizar la oración completa.
      2. Quitar prefijos de wake del JSON si aparecen al inicio.
      3. Buscar disparadores ``contiene_alguna`` dentro del texto restante.
      4. Si varias coinciden: gana el disparador **más largo**; empate → mayor ``prioridad``.

    Returns:
        ``ResultadoEmpareo`` con la acción a ejecutar, o ``None`` si no hay match.
    """
    norm = normalizar_oracion(oracion)
    prefijos = list(catalogo.get("prefijos_wake") or [])
    tras_wake = quitar_prefijos_wake(norm, prefijos)
    # Si tras quitar wake no queda nada útil, buscar en toda la frase normalizada.
    texto_busqueda = tras_wake if tras_wake.strip() else norm

    intenciones = list(catalogo.get("intenciones") or [])
    intenciones.sort(key=lambda x: int(x.get("prioridad", 0)), reverse=True)

    mejor: tuple[int, int, dict[str, Any], str] | None = None  # (len_disp, prioridad, intent, disparador)

    for intent in intenciones:
        prioridad = int(intent.get("prioridad", 0))
        disp = intent.get("disparadores") or {}
        lista = list(disp.get("contiene_alguna") or [])
        for raw in lista:
            d = normalizar_oracion(str(raw))
            if len(d) < 2:
                continue
            if d in texto_busqueda:
                cand = (len(d), prioridad, intent, raw)
                if mejor is None or cand[:2] > mejor[:2]:
                    mejor = cand

    if mejor is None:
        return None
    _, _, intent, disparador_crudo = mejor
    accion = intent.get("accion") or {}
    return ResultadoEmpareo(
        intencion_id=str(intent["id"]),
        intencion_titulo=str(intent.get("titulo", intent["id"])),
        disparador=str(disparador_crudo),
        texto_tras_wake=tras_wake,
        accion=accion if isinstance(accion, dict) else {},
    )
