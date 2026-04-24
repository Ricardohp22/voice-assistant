"""
Resolución del micrófono PortAudio por **índice** o por **subcadena del nombre**.

Iteración 3: cambiar de USB sin tocar código, solo ``config.py`` (nombre o índice).
"""

from __future__ import annotations

import numpy as np
import sounddevice as sd

from .capture import grabar_muestras, listar_dispositivos_entrada


def resolver_dispositivo_entrada(
    nombre_contiene: str | None,
    indice: int | None,
) -> int | None:
    """
    Devuelve el índice PortAudio a pasar a ``sounddevice`` como ``device``.

    Prioridad:
        1. Si ``nombre_contiene`` es una cadena no vacía (tras strip), el primer
           dispositivo de entrada cuyo nombre la contiene (sin distinguir mayúsculas).
        2. Si no, ``indice`` tal cual (puede ser ``None`` = entrada predeterminada del sistema).
    """
    if nombre_contiene is not None and nombre_contiene.strip():
        return buscar_indice_por_subcadena(nombre_contiene.strip())
    return indice


def buscar_indice_por_subcadena(subcadena: str) -> int:
    """
    Busca un único dispositivo de entrada cuyo nombre contiene ``subcadena``.

    Raises:
        ValueError: Si no hay coincidencias o hay más de una (ambigüedad).
    """
    needle = subcadena.lower()
    coincidencias: list[tuple[int, str]] = []
    for d in listar_dispositivos_entrada(imprimir=False):
        nombre = str(d.get("name", ""))
        if needle in nombre.lower():
            coincidencias.append((int(d["_indice"]), nombre))

    if not coincidencias:
        raise ValueError(
            f"No hay micrófono cuyo nombre contenga {subcadena!r}. "
            "Ejecute: python main.py --list-devices"
        )
    if len(coincidencias) > 1:
        lineas = "\n".join(f"  [{i}] {n}" for i, n in coincidencias)
        raise ValueError(
            f"Varios dispositivos coinciden con {subcadena!r}; acorte la cadena o use el índice:\n{lineas}"
        )
    return coincidencias[0][0]


def describir_dispositivo_entrada(dispositivo: int | None) -> str:
    """Nombre legible del dispositivo (o 'predeterminado del sistema')."""
    if dispositivo is None:
        return "predeterminado del sistema"
    try:
        info = sd.query_devices(dispositivo, kind="input")
        nombre = info.get("name", "?")
        return f"[{dispositivo}] {nombre}"
    except Exception:
        return f"[{dispositivo}] (no consultable)"


def comprobar_entrada_entrega_muestras(
    dispositivo: int | None,
    *,
    tasa_muestreo_hz: int,
    canales: int,
    duracion_segundos: float = 0.12,
) -> tuple[int, float]:
    """
    Abre el flujo, graba un instante y comprueba que hay datos finitos.

    Returns:
        (tasa_efectiva_hz, nivel_rms aproximado en float32 [-1,1]).

    Raises:
        ValueError: Si las muestras no son utilizables (NaN/inf o vacío).
        sounddevice.PortAudioError: Si no se puede abrir el dispositivo.
    """
    if duracion_segundos <= 0:
        raise ValueError("duracion_segundos debe ser > 0")
    muestras, tasa_efectiva = grabar_muestras(
        duracion_segundos,
        dispositivo=dispositivo,
        tasa_muestreo_hz=tasa_muestreo_hz,
        canales=canales,
    )
    arr = np.asarray(muestras, dtype=np.float32)
    if arr.size == 0:
        raise ValueError("La captura de prueba devolvió 0 muestras.")
    if not np.isfinite(arr).all():
        raise ValueError("La captura de prueba contiene NaN o inf (dispositivo o controlador anómalo).")
    rms = float(np.sqrt(np.mean(np.square(arr), dtype=np.float64)))
    return tasa_efectiva, rms
