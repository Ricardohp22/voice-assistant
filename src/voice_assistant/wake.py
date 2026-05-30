# Copyright del asistente: módulo de integración; openWakeWord es Apache-2.0 (David Scripka).
"""
Motor de detección de wake word usando openWakeWord (OWW).

================================================================================
Cómo encaja en el proyecto
================================================================================

El flujo de datos es:
    Micrófono (PortAudio/sounddevice)
      → callback float32 mono [-1, 1]
      → _EstadoWakeStream.extraer_bloques_pcm16()   ← acumula y remuestrea
      → cola de inferencia (hilo aparte)
      → Model.predict(pcm_int16_1280)               ← OWW
      → score > umbral + rebote anti-spam
      → evento detectado

Hay dos funciones públicas:
  - ``ejecutar_escucha_openwakeword``     → escucha continua con logs (--wake-listen).
  - ``esperar_primera_activacion_wake``   → espera UN solo hit y devuelve (--wake-turn).

La inferencia siempre va en un hilo auxiliar (``_hilo_inferencia``) para no
bloquear el callback de PortAudio, evitando xruns en Raspberry Pi.

================================================================================
Por qué 1280 muestras int16 a 16 kHz
================================================================================

Es el contrato del preprocesador de OWW: audio de voz "telefónica" a 16 kHz,
enteros de 16 bits. Cada bloque equivale a ~80 ms. ``_EstadoWakeStream`` acumula
audio nativo del micrófono hasta tener ese equivalente, remuestrea si la tasa
no es exactamente 16 kHz, y cuantiza a int16.

================================================================================
ONNX vs TFLite
================================================================================

``config.OPENWAKEWORD_INFERENCIA`` controla el backend. En ARM64/Pi se prefiere
"onnx" porque ``onnxruntime`` suele instalarse limpio. TFLite requiere el runtime
empaquetado con la versión de OWW (a veces ``ai_edge_litert``).

Ver también docs/wake_word_rendimiento_raspberry.md.
"""

from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import sounddevice as sd

from voice_assistant.audio import (
    cargar_wav_pcm16_mono_float32,
    mono_float32,
    remuestrear_mono_lineal,
    resolver_tasa_muestreo_entrada,
)


# =============================================================================
# IMPORTACIÓN LAZY DE OPENWAKEWORD
#
# Se importa en tiempo de ejecución (no en módulo) para que el resto de la app
# arranque aunque OWW no esté instalado (útil en desarrollo / CI sin hardware).
# =============================================================================

def _import_openwakeword() -> tuple[object, object, object]:
    try:
        import openwakeword
        from openwakeword.model import Model
        from openwakeword.utils import download_models
    except ImportError as exc:
        raise ImportError(
            "Falta el paquete openwakeword. En el venv ejecute: pip install -e ."
        ) from exc
    return openwakeword, Model, download_models


def _es_ruta_a_modelo(s: str) -> bool:
    """True si parece ruta a ONNX/TFLite (no nombre corto como hey_mycroft)."""
    baja = s.lower()
    if baja.endswith(".onnx") or baja.endswith(".tflite"):
        return True
    if os.path.isabs(s):
        return True
    if s.startswith(("./", "../", ".\\", "..\\")):
        return True
    return False


def asegurar_modelos_openwakeword(nombres_o_rutas: Sequence[str]) -> None:
    """
    Descarga modelos base (mel + embedding + VAD) y los wakewords indicados si faltan.

    Si solo se configuran rutas a modelos custom (sin nombres cortos), igualmente
    descarga un modelo oficial mínimo para traer los pesos compartidos sin bajar
    todos los wakewords del proyecto OWW.

    La primera ejecución puede tardar y requiere conexión a Internet.
    """
    _, _, download_models = _import_openwakeword()
    solo_nombres = [x for x in nombres_o_rutas if not _es_ruta_a_modelo(x)]
    if solo_nombres:
        download_models(model_names=list(solo_nombres))
    else:
        # Pedimos solo uno liviano para traer mel+embedding sin descargar TODO.
        download_models(model_names=["hey_mycroft"])


# =============================================================================
# BUFFER DE AUDIO — _EstadoWakeStream
#
# Problema a resolver: el callback de PortAudio puede llegar a cualquier ritmo
# (blocksize variable o fijo). OWW necesita exactamente 1280 muestras int16 a
# 16 kHz. Este buffer acumula fragmentos float32 en un deque (O(1) para append
# y popleft) y los agrupa en ventanas exactas, remuestreando si la tasa del mic
# es distinta de 16 kHz. Evita np.concatenate sobre el buffer completo en cada
# callback (coste O(n) que degrada la Raspberry al cabo de minutos).
# =============================================================================

def _pcm16_1280_desde_float32(f32: np.ndarray) -> np.ndarray:
    """Ajusta a 1280 muestras y cuantiza a int16 PCM (contrato OWW)."""
    x = np.asarray(f32, dtype=np.float32).reshape(-1)
    if x.size == 1280:
        y = x
    elif x.size > 1280:
        y = x[:1280]
    else:
        y = np.pad(x, (0, 1280 - x.size), mode="constant", constant_values=0.0)
    return (np.clip(y, -1.0, 1.0) * 32767.0).astype(np.int16)


def _muestras_nativas_por_bloque_oww(tasa_nativa_hz: int) -> int:
    """Cuántas muestras a tasa nativa equivalen a ~80 ms de audio a 16 kHz (1280)."""
    return max(1, int(round(1280 * int(tasa_nativa_hz) / 16_000.0)))


@dataclass
class _EstadoWakeStream:
    """
    Cola de fragmentos float32 mono: evita ``concatenate(pending, chunk)`` por
    callback. El lock protege el acceso desde el hilo de callback y el de inferencia.
    """

    tasa_hz: int
    lock: threading.Lock = field(default_factory=threading.Lock)
    fragmentos: deque[np.ndarray] = field(default_factory=deque)
    total_muestras: int = 0

    def extraer_bloques_pcm16(self, indata: np.ndarray) -> list[np.ndarray]:
        """
        Acumula audio del callback y devuelve ventanas int16 (1280,) listas para OWW.

        Cada ventana equivale a ~80 ms a 16 kHz. Si el micrófono graba a otra
        tasa, se remuestrea antes de cuantizar.
        """
        mono = mono_float32(indata).reshape(-1)
        ventanas: list[np.ndarray] = []
        need = _muestras_nativas_por_bloque_oww(self.tasa_hz)
        max_cola = need * 40    # ~3,2 s de cola máxima; descarta lo más antiguo si hay retraso.

        with self.lock:
            self.fragmentos.append(mono)
            self.total_muestras += int(mono.size)

            # Evitar crecimiento ilimitado si la inferencia va más lenta que la captura.
            while self.total_muestras > max_cola:
                viejo = self.fragmentos.popleft()
                self.total_muestras -= int(viejo.size)

            # Mientras haya audio suficiente, emitir una ventana OWW por iteración.
            while self.total_muestras >= need:
                partes: list[np.ndarray] = []
                faltan = need
                while faltan > 0:
                    primero = self.fragmentos[0]
                    if primero.size <= faltan:
                        partes.append(primero)
                        self.fragmentos.popleft()
                        self.total_muestras -= int(primero.size)
                        faltan -= int(primero.size)
                    else:
                        partes.append(primero[:faltan].copy())
                        self.fragmentos[0] = primero[faltan:]
                        self.total_muestras -= faltan
                        faltan = 0

                nativo = partes[0] if len(partes) == 1 else np.concatenate(partes, dtype=np.float32)
                bloque_16k = nativo if self.tasa_hz == 16_000 else remuestrear_mono_lineal(nativo, self.tasa_hz, 16_000)
                ventanas.append(_pcm16_1280_desde_float32(bloque_16k))
        return ventanas


# =============================================================================
# HELPER COMPARTIDO: COLA DE INFERENCIA Y HILO
#
# Tanto ``ejecutar_escucha_openwakeword`` como ``esperar_primera_activacion_wake``
# necesitan el mismo patrón: cola con drop-oldest si se llena, hilo daemon que
# consume y llama a modelo.predict. Se extrae aquí para no duplicar código.
# =============================================================================

_COLA_INFERENCIA_MAX = 16


def _encolar_pcm(infer_queue: queue.Queue, pcm: np.ndarray) -> None:
    """Encola una ventana PCM; si la cola está llena, descarta la más antigua."""
    try:
        infer_queue.put_nowait(pcm)
    except queue.Full:
        try:
            infer_queue.get_nowait()
        except queue.Empty:
            pass
        try:
            infer_queue.put_nowait(pcm)
        except queue.Full:
            pass


def _apagar_hilo_inferencia(
    infer_queue: queue.Queue,
    stop_event: threading.Event,
    hilo: threading.Thread,
) -> None:
    """Envía sentinela None a la cola y espera a que el hilo termine limpiamente."""
    stop_event.set()
    while True:
        try:
            infer_queue.put_nowait(None)
            break
        except queue.Full:
            try:
                infer_queue.get_nowait()
            except queue.Empty:
                break
    hilo.join(timeout=5.0)


def _cargar_audio_wake(ruta: str | Path | None) -> tuple[np.ndarray, int] | None:
    """Carga el WAV de confirmación de wake si la ruta existe; None si no."""
    if not ruta:
        return None
    p = Path(ruta)
    if not p.exists():
        print(f"Aviso: no existe el audio de wake en {p}.")
        return None
    try:
        return cargar_wav_pcm16_mono_float32(p)
    except Exception as exc:
        print(f"Aviso: no se pudo cargar audio de wake {p}: {exc}")
        return None


# =============================================================================
# FUNCIÓN PÚBLICA 1: ESCUCHA CONTINUA CON LOGS (--wake-listen)
#
# Corre OWW indefinidamente (o hasta timeout), imprime cada activación por
# consola y reproduce el beep de confirmación. No devuelve nada: es una prueba
# de hardware / demo del pipeline de detección.
# =============================================================================

def ejecutar_escucha_openwakeword(
    duracion_segundos: float,
    *,
    dispositivo: int | None,
    tasa_muestreo_solicitada_hz: int,
    canales: int,
    modelos: Sequence[str],
    frase_objetivo_producto: str,
    umbral: float,
    rebote_segundos: float,
    inferencia: str,
    vad_umbral: float,
    blocksize: int | None,
    ruta_audio_wake: str | Path | None = None,
) -> None:
    """
    Abre el micrófono, corre OWW en streaming y **anuncia** activaciones por consola.

    Args:
        duracion_segundos: 0 = hasta Ctrl+C.
        dispositivo: Índice PortAudio o None.
        tasa_muestreo_solicitada_hz: Tasa solicitada al abrir el stream.
        canales: Canales de captura.
        modelos: Rutas .onnx/.tflite o nombres incluidos (hey_mycroft, …).
        frase_objetivo_producto: Frase de UX (solo informativa en los logs).
        umbral: Score mínimo para considerar detección (OWW recomienda ~0.5).
        rebote_segundos: Tiempo mínimo entre dos activaciones consecutivas.
        inferencia: "onnx" o "tflite".
        vad_umbral: >0 activa VAD Silero integrado en OWW.
        blocksize: Marcos por callback; None = predeterminado de sounddevice.
        ruta_audio_wake: WAV que se reproduce al detectar el wakeword.
    """
    if inferencia not in ("onnx", "tflite"):
        raise ValueError('inferencia debe ser "onnx" o "tflite"')
    if not (0 <= umbral <= 1):
        raise ValueError("umbral debe estar en [0, 1]")
    if rebote_segundos < 0:
        raise ValueError("rebote_segundos debe ser >= 0")

    asegurar_modelos_openwakeword(modelos)
    _, Model, _ = _import_openwakeword()
    modelo = Model(
        wakeword_models=list(modelos),
        inference_framework=inferencia,
        vad_threshold=float(vad_umbral),
    )

    tasa_efectiva = resolver_tasa_muestreo_entrada(dispositivo, int(tasa_muestreo_solicitada_hz), canales=int(canales))
    if tasa_efectiva != tasa_muestreo_solicitada_hz:
        print(
            f"Aviso: stream de wake a {tasa_efectiva} Hz "
            f"(solicitado {tasa_muestreo_solicitada_hz} Hz); se remuestrea a 16 kHz antes de OWW."
        )

    nombres_llaves = list(modelo.models.keys())
    buf = _EstadoWakeStream(tasa_hz=int(tasa_efectiva))
    rebote_lock = threading.Lock()
    ultima_activacion_mono: list[float] = [-1e9]
    eventos_wake: queue.SimpleQueue[tuple[str, float]] = queue.SimpleQueue()

    infer_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=_COLA_INFERENCIA_MAX)
    stop_infer = threading.Event()

    def _hilo_inferencia() -> None:
        # Consume ventanas PCM de la cola, llama a OWW y publica eventos de wake.
        # Imprime el score y la frase de producto en consola para depuración.
        while True:
            try:
                pcm = infer_queue.get(timeout=0.08)
            except queue.Empty:
                if stop_infer.is_set():
                    break
                continue
            if pcm is None:
                break
            try:
                pred = modelo.predict(pcm)
            except Exception as exc:
                print(f"[WAKE] error en inferencia: {exc}")
                continue
            ahora = time.monotonic()
            for clave, valor in pred.items():
                arr = np.ravel(np.asarray(valor))
                if arr.size != 1:
                    continue
                try:
                    score = float(arr[0])
                except (TypeError, ValueError):
                    continue
                if score < umbral:
                    continue
                with rebote_lock:
                    if ahora - ultima_activacion_mono[0] < rebote_segundos:
                        continue
                    ultima_activacion_mono[0] = ahora
                print(
                    f"[WAKE] modelo={clave!r} score={score:.3f} (umbral={umbral}) "
                    f"t={time.strftime('%H:%M:%S')} — "
                    f"frase objetivo: {frase_objetivo_producto!r}"
                )
                eventos_wake.put((clave, score))

    infer_thread = threading.Thread(target=_hilo_inferencia, name="oww-infer-listen", daemon=True)
    infer_thread.start()

    wake_audio = _cargar_audio_wake(ruta_audio_wake)

    def callback(indata: np.ndarray, frames: int, _t, status: object) -> None:
        del frames
        for pcm in buf.extraer_bloques_pcm16(indata):
            _encolar_pcm(infer_queue, pcm)

    stream = sd.InputStream(
        samplerate=int(tasa_efectiva),
        blocksize=blocksize if blocksize is not None and blocksize > 0 else None,
        device=dispositivo,
        channels=int(canales),
        dtype="float32",
        latency="low",
        callback=callback,
    )

    t0 = time.perf_counter()
    duracion_str = "∞" if duracion_segundos <= 0 else f"{duracion_segundos} s"
    print(
        f"Escucha wake word (OWW). Modelos: {nombres_llaves}. "
        f"Duración={duracion_str}. Ctrl+C para salir."
    )

    try:
        with stream:
            stream.start()
            try:
                while True:
                    time.sleep(0.2)
                    # Vaciar la cola de eventos y reproducir el beep si hay hits.
                    while True:
                        try:
                            infer_queue.get_nowait()
                        except queue.Empty:
                            break
                        if wake_audio is not None:
                            audio, sr = wake_audio
                            sd.play(audio, samplerate=sr, blocking=False)
                    # Vaciar eventos_wake (el beep ya se procesó arriba).
                    while True:
                        try:
                            eventos_wake.get_nowait()
                        except queue.Empty:
                            break
                        if wake_audio is not None:
                            audio, sr = wake_audio
                            sd.play(audio, samplerate=sr, blocking=False)
                    if duracion_segundos > 0 and (time.perf_counter() - t0) >= duracion_segundos:
                        break
            except KeyboardInterrupt:
                print("\nInterrupción por teclado.")
            finally:
                stream.stop()
    finally:
        _apagar_hilo_inferencia(infer_queue, stop_infer, infer_thread)

    print(f"Fin escucha wake ({time.perf_counter() - t0:.1f} s reloj).")


# =============================================================================
# FUNCIÓN PÚBLICA 2: PRIMERA ACTIVACIÓN (--wake-turn / pipeline.py)
#
# Igual que la escucha continua pero con comportamiento diferente:
#   - Para en la PRIMERA detección y devuelve (no sigue escuchando).
#   - No imprime scores continuos (modo silencioso).
#   - Reproduce el beep de confirmación de forma BLOQUEANTE antes de devolver,
#     para que pipeline.py pueda abrir la grabación de la orden sin solapamiento.
# =============================================================================

def esperar_primera_activacion_wake(
    timeout_seg: float,
    *,
    dispositivo: int | None,
    tasa_muestreo_solicitada_hz: int,
    canales: int,
    modelos: Sequence[str],
    frase_objetivo_producto: str,
    umbral: float,
    rebote_segundos: float,
    inferencia: str,
    vad_umbral: float,
    blocksize: int | None,
    ruta_audio_wake: str | Path | None,
) -> tuple[str, float] | None:
    """
    Espera la primera detección de wake word y devuelve.

    A diferencia de ``ejecutar_escucha_openwakeword``, no imprime scores por
    consola y termina en el primer hit (o cuando vence ``timeout_seg``).
    El beep de confirmación se reproduce de forma **bloqueante** para que la
    grabación de la orden (pipeline.py fase 3) no se solape con él.

    Returns:
        (clave_modelo, score) o None si vence el timeout sin detección.
    """
    if timeout_seg <= 0:
        raise ValueError("timeout_seg debe ser > 0")
    if inferencia not in ("onnx", "tflite"):
        raise ValueError('inferencia debe ser "onnx" o "tflite"')
    if not (0 <= umbral <= 1):
        raise ValueError("umbral debe estar en [0, 1]")
    if rebote_segundos < 0:
        raise ValueError("rebote_segundos debe ser >= 0")

    asegurar_modelos_openwakeword(modelos)
    _, Model, _ = _import_openwakeword()
    modelo = Model(
        wakeword_models=list(modelos),
        inference_framework=inferencia,
        vad_threshold=float(vad_umbral),
    )

    tasa_efectiva = resolver_tasa_muestreo_entrada(dispositivo, int(tasa_muestreo_solicitada_hz), canales=int(canales))
    if tasa_efectiva != tasa_muestreo_solicitada_hz:
        print(
            f"Aviso: wake a {tasa_efectiva} Hz "
            f"(solicitado {tasa_muestreo_solicitada_hz} Hz); se remuestrea a 16 kHz."
        )

    buf = _EstadoWakeStream(tasa_hz=int(tasa_efectiva))
    rebote_lock = threading.Lock()
    ultima_activacion_mono: list[float] = [-1e9]
    eventos_wake: queue.SimpleQueue[tuple[str, float]] = queue.SimpleQueue()

    infer_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=_COLA_INFERENCIA_MAX)
    stop_infer = threading.Event()

    def _hilo_inferencia_silenciosa() -> None:
        # Sin prints por score: solo publica en eventos_wake cuando hay hit.
        # El pipeline.py solo necesita saber SI hubo wake y con qué score.
        while True:
            try:
                pcm = infer_queue.get(timeout=0.08)
            except queue.Empty:
                if stop_infer.is_set():
                    break
                continue
            if pcm is None:
                break
            try:
                pred = modelo.predict(pcm)
            except Exception as exc:
                print(f"[WAKE] error en inferencia: {exc}")
                continue
            ahora = time.monotonic()
            for clave, valor in pred.items():
                arr = np.ravel(np.asarray(valor))
                if arr.size != 1:
                    continue
                try:
                    score = float(arr[0])
                except (TypeError, ValueError):
                    continue
                if score < umbral:
                    continue
                with rebote_lock:
                    if ahora - ultima_activacion_mono[0] < rebote_segundos:
                        continue
                    ultima_activacion_mono[0] = ahora
                eventos_wake.put((str(clave), score))

    infer_thread = threading.Thread(
        target=_hilo_inferencia_silenciosa,
        name="oww-infer-once",
        daemon=True,
    )
    infer_thread.start()

    wake_audio = _cargar_audio_wake(ruta_audio_wake)

    def callback(indata: np.ndarray, frames: int, _t, status: object) -> None:
        del frames
        for pcm in buf.extraer_bloques_pcm16(indata):
            _encolar_pcm(infer_queue, pcm)

    stream = sd.InputStream(
        samplerate=int(tasa_efectiva),
        blocksize=blocksize if blocksize is not None and blocksize > 0 else None,
        device=dispositivo,
        channels=int(canales),
        dtype="float32",
        latency="low",
        callback=callback,
    )

    resultado: tuple[str, float] | None = None
    try:
        with stream:
            stream.start()
            deadline = time.perf_counter() + timeout_seg
            while time.perf_counter() < deadline:
                try:
                    clave, score = eventos_wake.get_nowait()
                    resultado = (clave, score)
                    break
                except queue.Empty:
                    time.sleep(0.02)
            stream.stop()
    finally:
        _apagar_hilo_inferencia(infer_queue, stop_infer, infer_thread)

    if resultado is None:
        return None

    clave, score = resultado
    # Beep bloqueante: pipeline.py no abre la grabación hasta que este retorna.
    if wake_audio is not None:
        audio, sr = wake_audio
        sd.play(audio, samplerate=sr, blocking=True)
    return (clave, score)
