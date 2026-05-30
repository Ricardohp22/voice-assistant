"""
Intenciones del asistente: catálogo, emparejamiento y manejadores.

Este módulo tiene tres responsabilidades que conviven porque están íntimamente
acopladas: separadas solo añadirían archivos con importaciones circulares.

  ┌─────────────────────────────────────────────────────────────────────────┐
  │  SECCIÓN 1 — CATÁLOGO                                                   │
  │  Carga el JSON de intenciones y empareja texto de STT con disparadores. │
  │                                                                         │
  │  SECCIÓN 2 — UTILIDADES DE AUDIO                                        │
  │  Reproduce WAVs de respuesta (antes en ejecutor.py, inlineado aquí     │
  │  porque solo se llama desde los manejadores de esta misma sección).     │
  │                                                                         │
  │  SECCIÓN 3 — MANEJADORES                                                │
  │  Lógica de ejecución de cada intención. Aquí viven manejar_saludar,    │
  │  manejar_nueva_reunion y el registro REGISTRO_MANEJADORES.              │
  └─────────────────────────────────────────────────────────────────────────┘

Para añadir una intención nueva:
  1. Entrada en data/catalogo_intenciones.json (id, disparadores, prioridad).
  2. Función ``manejar_<id>`` en la Sección 3.
  3. Registrarla en REGISTRO_MANEJADORES.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sounddevice as sd

from voice_assistant import config
from voice_assistant.audio import (
    cargar_wav_pcm16_mono_float32,
    grabar_muestras,
    preparar_muestras_para_stt,
    resolver_dispositivo_entrada_config,
)
from voice_assistant.stt import transcribir_float32_16khz


# =============================================================================
# SECCIÓN 1: CATÁLOGO
#
# El catálogo vive en data/catalogo_intenciones.json. Esta sección lo carga,
# normaliza el texto de STT (quita acentos, minúsculas, espacios compactados)
# y busca el disparador con mayor coincidencia. Ninguna lógica de acción aquí.
# =============================================================================

def raiz_repositorio() -> Path:
    """Raíz del repo voice-assistant/ (el directorio que contiene src/ y data/).

    Niveles desde este archivo:
        parents[0] = src/voice_assistant/
        parents[1] = src/
        parents[2] = voice-assistant/   ← raíz del repo
    """
    return Path(__file__).resolve().parents[2]


def normalizar_oracion(texto: str) -> str:
    """
    Prepara texto de STT para comparar con disparadores del catálogo.

    Minúsculas, sin acentos, espacios compactados. Así "Hola" y "hola" coinciden
    y variantes con tildes de Whisper no rompen el emparejo.
    """
    s = texto.strip().lower()
    nkfd = unicodedata.normalize("NFD", s)
    sin_acentos = "".join(c for c in nkfd if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sin_acentos).strip()


def _quitar_prefijos_wake(texto_norm: str, prefijos: list[str]) -> str:
    """
    Quita del inicio la variante de wake word que Whisper suele transcribir.

    Los prefijos vienen del JSON (campo ``prefijos_wake``). Se prueban del más
    largo al más corto para no cortar de más.
    """
    t = texto_norm.strip()
    prefs_ord = sorted((normalizar_oracion(p) for p in prefijos), key=len, reverse=True)
    for p in prefs_ord:
        if t.startswith(p):
            resto = t[len(p):].strip(" ,.;:-—")
            return resto if resto else t
    return t


@dataclass(frozen=True)
class ResultadoEmpareo:
    """Intención elegida y metadatos útiles para logs y despacho en manejadores."""

    intencion_id: str
    intencion_titulo: str
    disparador: str
    texto_tras_wake: str


def cargar_catalogo(ruta_relativa_o_absoluta: str | Path) -> dict[str, Any]:
    """
    Lee el JSON del catálogo de intenciones.

    Rutas relativas se resuelven desde la raíz del repositorio (no desde el cwd),
    para que el comando funcione igual sea cual sea el directorio de trabajo.
    """
    p = Path(ruta_relativa_o_absoluta)
    if not p.is_absolute():
        p = raiz_repositorio() / p
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def emparejar_intencion(catalogo: dict[str, Any], oracion: str) -> ResultadoEmpareo | None:
    """
    Elige la intención que mejor coincide con la transcripción del turno.

    Algoritmo:
      1. Normaliza la oración completa (sin acentos, minúsculas).
      2. Quita los prefijos de wake del JSON si aparecen al inicio.
      3. Busca disparadores ``contiene_alguna`` dentro del texto restante.
      4. Gana el disparador más largo; en empate, mayor ``prioridad``.

    Returns:
        ResultadoEmpareo con el ``id`` a despachar, o None si no hay coincidencia.
    """
    norm = normalizar_oracion(oracion)
    prefijos = list(catalogo.get("prefijos_wake") or [])
    tras_wake = _quitar_prefijos_wake(norm, prefijos)
    texto_busqueda = tras_wake if tras_wake.strip() else norm

    intenciones = list(catalogo.get("intenciones") or [])
    intenciones.sort(key=lambda x: int(x.get("prioridad", 0)), reverse=True)

    mejor: tuple[int, int, dict[str, Any], str] | None = None

    for intent in intenciones:
        prioridad = int(intent.get("prioridad", 0))
        lista = list((intent.get("disparadores") or {}).get("contiene_alguna") or [])
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
    return ResultadoEmpareo(
        intencion_id=str(intent["id"]),
        intencion_titulo=str(intent.get("titulo", intent["id"])),
        disparador=str(disparador_crudo),
        texto_tras_wake=tras_wake,
    )


# =============================================================================
# SECCIÓN 2: UTILIDADES DE AUDIO
#
# Estas funciones existían en ejecutor.py (33 líneas) y solo se llaman desde
# los manejadores de la Sección 3. Se inlinean aquí para evitar un archivo
# extra con una sola función.
# =============================================================================

def _resolver_ruta_audio(ruta: str) -> Path:
    """Rutas relativas a la raíz del repo; absolutas se usan tal cual."""
    p = Path(ruta)
    return p if p.is_absolute() else raiz_repositorio() / p


def _reproducir_audio(ruta: str, *, bloqueante: bool = False) -> None:
    """
    Reproduce un WAV mono por el dispositivo de salida por defecto.

    Si el archivo no existe, imprime aviso y continúa sin lanzar excepción
    (útil en entornos de desarrollo sin todos los WAVs presentes).
    """
    archivo = _resolver_ruta_audio(ruta)
    if not archivo.is_file():
        print(f"Aviso: no existe el audio {archivo}; omitiendo reproducción.")
        return
    audio, sr = cargar_wav_pcm16_mono_float32(archivo)
    sd.play(audio, samplerate=sr, blocking=bloqueante)


def _grabar_y_transcribir(duracion_seg: float) -> str:
    """
    Graba ``duracion_seg`` segundos, normaliza a pipeline STT y devuelve el texto Whisper.

    Usado dentro de manejar_nueva_reunion para capturar el nombre de la reunión.
    Reutiliza el mismo micrófono y parámetros que el resto del pipeline.
    """
    print(f"Escuchando {duracion_seg:.0f} s...")
    muestras, tasa_efectiva = grabar_muestras(
        duracion_seg,
        dispositivo=resolver_dispositivo_entrada_config(),
        tasa_muestreo_hz=config.TASA_MUESTREO_HZ,
        canales=config.CANALES,
    )
    audio_16k, _ = preparar_muestras_para_stt(
        muestras,
        tasa_efectiva,
        config.TASA_SALIDA_PIPELINE_HZ,
    )
    return transcribir_float32_16khz(
        audio_16k,
        modelo=config.WHISPER_MODELO,
        dispositivo=config.WHISPER_DISPOSITIVO,
        tipo_computo=config.WHISPER_TIPO_COMPUTO,
        idioma=config.WHISPER_IDIOMA,
    )


# =============================================================================
# SECCIÓN 3: MANEJADORES
#
# Cada intención definida en el JSON debe tener aquí una función ``manejar_<id>``
# registrada en REGISTRO_MANEJADORES. El flujo es siempre:
#   pipeline.py → ejecutar_intencion(id) → REGISTRO_MANEJADORES[id]()
#
# Rutas de audio de respuesta (relativas a la raíz del repo):
# =============================================================================

_AUDIO_SALUDO = "audio_messages/saludo.wav"
_AUDIO_ASK_NAME = "audio_messages/ask_name.wav"
_AUDIO_SUCCESFUL_MEETING = "audio_messages/new_reunion.wav"

# Variables de estado del último turno (útiles para depuración / tests).
transcripcion_seguimiento_nueva_reunion: str | None = None
ultima_respuesta_reunion: object | None = None


def manejar_saludar(*, bloqueante: bool = True) -> None:
    """Intención ``saludar``: reproduce el saludo por audio."""
    _reproducir_audio(_AUDIO_SALUDO, bloqueante=bloqueante)


def manejar_nueva_reunion(*, bloqueante: bool = True) -> None:
    """
    Intención ``nueva_reunion``:
      1. Pide el nombre de la reunión por audio.
      2. Escucha y transcribe el nombre.
      3. Publica el comando a Node vía Redis.
      4. Espera la respuesta (máx. REDIS_RESPUESTA_TIMEOUT_SEG).
      5. Reproduce audio de éxito si Node confirma la creación.
    """
    global transcripcion_seguimiento_nueva_reunion, ultima_respuesta_reunion

    # Importación local para no acoplar intents → redis_reunion en el módulo:
    # redis_reunion solo se necesita en este manejador específico.
    from voice_assistant.redis_reunion import publicar_y_esperar_respuesta_iniciar_reunion

    print("Cual es el nombre de la reunion...")
    _reproducir_audio(_AUDIO_ASK_NAME, bloqueante=True)

    transcripcion_seguimiento_nueva_reunion = _grabar_y_transcribir(config.NUEVA_REUNION_ESCUCHA_SEG)
    nombre_reunion = (transcripcion_seguimiento_nueva_reunion or "").strip()
    print(f"Nombre de la reunion: {nombre_reunion!r}")

    if not nombre_reunion:
        print("Aviso: no se capturó nombre; no se publica en Redis.")
        return

    ultima_respuesta_reunion = None
    try:
        print(
            f"Enviando crear reunión a Node (canal {config.REDIS_CANAL_COMANDOS!r}), "
            f"esperando respuesta hasta {config.REDIS_RESPUESTA_TIMEOUT_SEG:.0f} s..."
        )
        resultado = publicar_y_esperar_respuesta_iniciar_reunion(
            nombre_reunion,
            transcripcion=transcripcion_seguimiento_nueva_reunion,
        )
        ultima_respuesta_reunion = resultado.respuesta

        if resultado.respuesta is None:
            print(
                f"Sin respuesta de Node en {config.REDIS_RESPUESTA_TIMEOUT_SEG:.0f} s "
                f"(solicitud_id={resultado.solicitud_id}). No se reproduce audio de éxito."
            )
            return

        r = resultado.respuesta
        if r.exito:
            print(f"Reunión creada correctamente: {r.mensaje or '(sin mensaje)'}")
            _reproducir_audio(_AUDIO_SUCCESFUL_MEETING, bloqueante=True)
        else:
            print(f"No se pudo crear la reunión [{r.estado}]: {r.mensaje or '(sin mensaje)'}")
            if r.detalle:
                print(f"Detalle: {r.detalle}")
    except Exception as exc:
        print(f"Error en comunicación Redis: {exc}")


# Registro central: añade aquí cada nuevo par (id, función).
ManejadorIntencion = Callable[..., None]
REGISTRO_MANEJADORES: dict[str, ManejadorIntencion] = {
    "saludar": manejar_saludar,
    "nueva_reunion": manejar_nueva_reunion,
}


def ejecutar_intencion(intencion_id: str, *, bloqueante: bool = True) -> None:
    """
    Despacha al manejador registrado para ``intencion_id``.

    Raises:
        ValueError: Si no hay manejador para ese id (falta implementar o registrar).
    """
    manejador = REGISTRO_MANEJADORES.get(intencion_id)
    if manejador is None:
        raise ValueError(
            f"No hay manejador registrado para la intención {intencion_id!r}. "
            f"Añádalo en intents.py (ids conocidos: {sorted(REGISTRO_MANEJADORES)})."
        )
    manejador(bloqueante=bloqueante)
