# Copyright del asistente: módulo de integración; openWakeWord es Apache-2.0 (David Scripka).
"""
================================================================================
openWakeWord en este proyecto — qué hace y cómo encaja (iteración 5)
================================================================================

1) Qué es **openWakeWord** (OWW)
   Es una biblioteca que ejecuta **modelos pequeños** (TFLite u ONNX) sobre el
   audio del micrófono. Cada ~**80 ms** de audio a **16 kHz** (1280 muestras en
   PCM **int16**) pasa por:
   - un **melspectrograma** fijo,
   - un **extractor de incrustaciones** (“embedding”) compartido,
   - y una **cabeza clasificadora** por cada wake word / frase entrenada.
   La salida es un **score entre 0 y 1** por modelo: por encima de un **umbral**
   consideramos que hubo activación.

2) Flujo de datos en **nuestro** código
   Micrófono (PortAudio / sounddevice) → callback con bloques float32 mono
   [-1, 1] → se **acumulan** muestras hasta tener **80 ms equivalentes** a la
   tasa *real* del dispositivo → si la tasa no es 16 kHz, **remuestreamos** a
   16 kHz (misma idea que el pipeline STT) → **int16** de longitud **1280**
   → ``Model.predict`` → leemos los scores → si superan el umbral y pasó el
   **rebote** (anti-spam), imprimimos una línea en consola.

   **No guardamos WAV** ni listas de audio largas: los trozos del micrófono se
   encadenan en una cola de fragmentos (``deque``) con límite de muestras, sin
   ``np.concatenate`` del buffer entero en cada callback (eso degradaba la CPU
   con el tiempo y hacía fallar el wake). La inferencia puede ir en un hilo
   aparte para no bloquear el callback de PortAudio.

3) Relación con ``FRASE_ACTIVACION`` en ``config.py``
   ``FRASE_ACTIVACION`` (p. ej. ``"hi box translate"``) es la **frase de
   producto**: documentación, futuro TTS, UX. **openWakeWord no “sabe” ese
   texto**: solo ejecuta **modelos entrenados** con ejemplos de audio de esa
   frase (o similares). Los modelos **incluidos** en OWW son otros lemas
   (``hey_mycroft``, ``alexa``, ``hey_jarvis``, …). Para **"hi box translate"**
   hace falta **entrenar** un modelo custom (Colab / openwakeword.com) y poner
   la ruta al ``.onnx`` o ``.tflite`` en ``OPENWAKEWORD_MODELOS``. Hasta
   entonces, dejamos un modelo incluido solo para **probar el cableado** del
   pipeline en la Raspberry.

4) Por qué **1280** muestras y **int16**
   Es el contrato del preprocesador de OWW: audio de voz telefónica 16 kHz,
   enteros de 16 bits. El README recomienda alimentar en múltiplos de 80 ms para
   eficiencia; nosotros entregamos exactamente un bloque de 1280 tras alinear.

5) ONNX vs TFLite
   En ``config.OPENWAKEWORD_INFERENCIA`` usamos por defecto **onnx** en ARM64:
   suele instalar bien ``onnxruntime`` en Pi. TFLite depende del runtime
   empaquetado con la versión de OWW (a veces ``ai_edge_litert``); si prefiere
   TFLite, cámbielo y compruebe dependencias.

6) Descarga de pesos
   ``asegurar_modelos_openwakeword`` llama a ``download_models`` con los nombres
   necesarios (embedding, melspec, VAD opcional, y sus wakewords). Si solo
   configura **rutas locales** a modelos custom, igualmente disparamos una
   descarga mínima de un modelo oficial pequeño para obligar a bajar los pesos
   compartidos sin descargar *todos* los wakewords del proyecto. La primera
   ejecución puede tardar y requiere red.
================================================================================
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

from voice_assistant.audio.capture import resolver_tasa_muestreo_entrada
from voice_assistant.audio.formato_pipeline import mono_float32, remuestrear_mono_lineal
from voice_assistant.audio.wav_io import cargar_wav_pcm16_mono_float32


def _import_openwakeword() -> tuple[object, object, object]:
    try:
        import openwakeword
        from openwakeword.model import Model
        from openwakeword.utils import download_models
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Falta el paquete openwakeword. En el venv del proyecto ejecute: pip install -e ."
        ) from exc
    return openwakeword, Model, download_models


def _es_ruta_a_modelo(s: str) -> bool:
    """True si parece ruta a ONNX/TFLite (no nombre corto tipo hey_mycroft)."""
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

    ``nombres_o_rutas`` puede contener nombres cortos (p. ej. ``\"hey_mycroft\"``)
    o rutas a ``.onnx``/``.tflite``. Las rutas deben existir en disco; no se
    descargan de GitHub.
    """
    _, _, download_models = _import_openwakeword()
    solo_nombres = [x for x in nombres_o_rutas if not _es_ruta_a_modelo(x)]
    if solo_nombres:
        download_models(model_names=list(solo_nombres))
    else:
        # Sin nombres oficiales, ``download_models([])`` bajaría *todos* los
        # wakewords; evitamos eso y pedimos solo uno liviano para traer mel+embedding.
        download_models(model_names=["hey_mycroft"])


def _pcm16_1280_desde_float32(f32: np.ndarray) -> np.ndarray:
    """Ajusta a 1280 muestras y cuantiza a int16 PCM (contrato OWW)."""
    x = np.asarray(f32, dtype=np.float32).reshape(-1)
    if x.size == 1280:
        y = x
    elif x.size > 1280:
        y = x[:1280]
    else:
        y = np.pad(x, (0, 1280 - x.size), mode="constant", constant_values=0.0)
    y = np.clip(y, -1.0, 1.0)
    return (y * 32767.0).astype(np.int16)


def _muestras_nativas_por_bloque_oww(tasa_nativa_hz: int) -> int:
    """Cuántas muestras a tasa nativa equivalen a ~80 ms de audio a 16 kHz (1280)."""
    return max(1, int(round(1280 * int(tasa_nativa_hz) / 16_000.0)))


@dataclass
class _EstadoWakeStream:
    """
    Cola de fragmentos float32 mono: evita ``concatenate(pending, chunk)`` por
    callback (coste proporcional al tamaño del backlog y degrada el wake al
    cabo de minutos en Raspberry).
    """

    tasa_hz: int
    lock: threading.Lock = field(default_factory=threading.Lock)
    fragmentos: deque[np.ndarray] = field(default_factory=deque)
    total_muestras: int = 0

    def extraer_bloques_pcm16(self, indata: np.ndarray) -> list[np.ndarray]:
        """
        Acumula audio del callback y devuelve ventanas int16 (1280,) para OWW.

        Usado por ``esperar_primera_activacion_wake`` (``--wake-turn``) y por
        ``ejecutar_escucha_openwakeword``. Cada ventana ≈ 80 ms a 16 kHz.
        """
        mono = mono_float32(indata).reshape(-1)
        ventanas: list[np.ndarray] = []
        # Cuántas muestras a la tasa *nativa* del mic equivalen a 1280 @ 16 kHz.
        need = _muestras_nativas_por_bloque_oww(self.tasa_hz)
        max_cola = need * 40  # ~3,2 s de cola máxima; descarta lo más antiguo si hay retraso.

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
                if self.tasa_hz == 16_000:
                    bloque_16k = nativo
                else:
                    bloque_16k = remuestrear_mono_lineal(nativo, self.tasa_hz, 16_000)
                ventanas.append(_pcm16_1280_desde_float32(bloque_16k))
        return ventanas


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
    Abre el micrófono, corre openWakeWord en streaming y **anuncia** activaciones por consola.

    Args:
        duracion_segundos: 0 = hasta Ctrl+C.
        dispositivo: índice PortAudio o None.
        tasa_muestreo_solicitada_hz: tasa solicitada al abrir el stream (p. ej. 16000).
        canales: canales de captura.
        modelos: rutas a ONNX/TFLite **o** nombres de modelos incluidos (``hey_mycroft``, …).
        frase_objetivo_producto: frase de UX (p. ej. config.FRASE_ACTIVACION); solo informativa aquí.
        umbral: score mínimo para considerar detección (OWW recomienda ~0.5 como punto de partida).
        rebote_segundos: tiempo mínimo entre dos mensajes de “activado”.
        inferencia: ``\"onnx\"`` o ``\"tflite\"``.
        vad_umbral: > 0 activa VAD Silero integrado en OWW (reduce falsos en ruido no verbal).
        blocksize: marcos por callback de sounddevice; None = valor por defecto de la librería.
        ruta_audio_wake: WAV que se reproduce al detectar wakeword. Puede ser relativa al cwd.
    """
    if inferencia not in ("onnx", "tflite"):
        raise ValueError('inferencia debe ser "onnx" o "tflite"')
    if umbral < 0 or umbral > 1:
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

    tasa_efectiva = resolver_tasa_muestreo_entrada(
        dispositivo,
        int(tasa_muestreo_solicitada_hz),
        canales=int(canales),
    )
    if tasa_efectiva != tasa_muestreo_solicitada_hz:
        print(
            f"Aviso: stream de wake a {tasa_efectiva} Hz (solicitado {tasa_muestreo_solicitada_hz} Hz); "
            "se remuestrea a 16 kHz antes de OWW."
        )

    nombres_llaves = list(modelo.models.keys())

    buf = _EstadoWakeStream(tasa_hz=int(tasa_efectiva))
    rebote_lock = threading.Lock()
    # Lista de un elemento: compartida entre hilos sin ``nonlocal``.
    ultima_activacion_mono: list[float] = [-1e9]
    eventos_wake: queue.SimpleQueue[tuple[str, float]] = queue.SimpleQueue()

    # Inferencia fuera del callback de PortAudio: evita que ONNX supere el
    # tiempo de un bloque y provoque xruns / audio “comido” (wake deja de ir).
    _COLA_INFERENCIA_MAX = 16
    infer_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=_COLA_INFERENCIA_MAX)
    stop_infer = threading.Event()

    def _encolar_pcm_oww(pcm: np.ndarray) -> None:
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

    def _hilo_inferencia() -> None:
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
            except Exception as exc:  # pragma: no cover
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
                    f"frase de producto objetivo: {frase_objetivo_producto!r} "
                    f"(solo coincide si cargó un modelo entrenado para ella)."
                )
                eventos_wake.put((clave, score))

    infer_thread = threading.Thread(target=_hilo_inferencia, name="openWakeWord-infer", daemon=True)
    infer_thread.start()

    wake_audio: tuple[np.ndarray, int] | None = None
    if ruta_audio_wake:
        ruta_wake = Path(ruta_audio_wake)
        if ruta_wake.exists():
            try:
                wake_audio = cargar_wav_pcm16_mono_float32(ruta_wake)
            except Exception as exc:
                print(f"Aviso: no se pudo cargar audio de wake {ruta_wake}: {exc}")
        else:
            print(f"Aviso: no existe el audio de wake en {ruta_wake}.")

    def callback(indata: np.ndarray, frames: int, _t, status: object) -> None:
        del frames
        if status:
            pass
        for pcm in buf.extraer_bloques_pcm16(indata):
            _encolar_pcm_oww(pcm)

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
    print(
        f"Escucha wake word (openWakeWord). Modelos cargados: {nombres_llaves}. "
        f"Ctrl+C para salir antes de tiempo. Duración={'∞' if duracion_segundos <= 0 else str(duracion_segundos) + ' s'}"
    )

    try:
        with stream:
            stream.start()
            try:
                while True:
                    time.sleep(0.2)
                    while True:
                        try:
                            _modelo, _score = eventos_wake.get_nowait()
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
        stop_infer.set()
        while True:
            try:
                infer_queue.put_nowait(None)
                break
            except queue.Full:
                try:
                    infer_queue.get_nowait()
                except queue.Empty:
                    break
        infer_thread.join(timeout=5.0)

    print(f"Fin escucha wake ({time.perf_counter() - t0:.1f} s reloj).")


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
    Fase de wake para ``--wake-turn``: primera detección y salida.

    A diferencia de ``ejecutar_escucha_openwakeword`` (escucha continua con logs),
    aquí el hilo principal **espera una sola** activación y devuelve. La inferencia
    corre en un hilo auxiliar para no bloquear el callback de PortAudio.

    Si ``ruta_audio_wake`` apunta a un WAV existente, se reproduce en **bloqueante**
    antes de devolver, para que la grabación de la orden no se solape con el beep.

    Returns:
        ``(clave_modelo, score)`` o ``None`` si vence ``timeout_seg`` sin detección.
    """
    # --- Validación de parámetros (mismos criterios que escucha prolongada) ---
    if timeout_seg <= 0:
        raise ValueError("timeout_seg debe ser > 0")
    _ = frase_objetivo_producto  # solo UX/documentación; OWW usa los ONNX de ``modelos``
    if inferencia not in ("onnx", "tflite"):
        raise ValueError('inferencia debe ser "onnx" o "tflite"')
    if umbral < 0 or umbral > 1:
        raise ValueError("umbral debe estar en [0, 1]")
    if rebote_segundos < 0:
        raise ValueError("rebote_segundos debe ser >= 0")

    # --- Carga de pesos y modelo openWakeWord ---
    asegurar_modelos_openwakeword(modelos)
    _, Model, _ = _import_openwakeword()

    modelo = Model(
        wakeword_models=list(modelos),
        inference_framework=inferencia,
        vad_threshold=float(vad_umbral),
    )

    # Tasa real del mic; si no es 16 kHz, ``_EstadoWakeStream`` remuestrea antes de OWW.
    tasa_efectiva = resolver_tasa_muestreo_entrada(
        dispositivo,
        int(tasa_muestreo_solicitada_hz),
        canales=int(canales),
    )
    if tasa_efectiva != tasa_muestreo_solicitada_hz:
        print(
            f"Aviso: wake a {tasa_efectiva} Hz (solicitado {tasa_muestreo_solicitada_hz} Hz); "
            "se remuestrea a 16 kHz antes de OWW."
        )

    # --- Estado compartido: buffer de audio + cola de eventos de wake detectados ---
    buf = _EstadoWakeStream(tasa_hz=int(tasa_efectiva))
    rebote_lock = threading.Lock()
    ultima_activacion_mono: list[float] = [-1e9]
    eventos_wake: queue.SimpleQueue[tuple[str, float]] = queue.SimpleQueue()

    # Cola entre callback (rápido) e hilo de inferencia (puede tardar en Pi).
    _COLA_INFERENCIA_MAX = 16
    infer_queue: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=_COLA_INFERENCIA_MAX)
    stop_infer = threading.Event()

    def _encolar_pcm_oww(pcm: np.ndarray) -> None:
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

    def _hilo_inferencia_silenciosa() -> None:
        """
        Consume ventanas de la cola y llama a ``modelo.predict``.

        Sin prints por score (a diferencia de ``--wake-listen``): solo publica
        en ``eventos_wake`` cuando score >= umbral y pasó el rebote.
        """
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
            except Exception as exc:  # pragma: no cover
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
        name="openWakeWord-infer-once",
        daemon=True,
    )
    infer_thread.start()

    # Audio de confirmación (p. ej. wake_hola.wav); se reproduce al final si hubo hit.
    wake_audio: tuple[np.ndarray, int] | None = None
    if ruta_audio_wake:
        ruta_wake = Path(ruta_audio_wake)
        if ruta_wake.exists():
            try:
                wake_audio = cargar_wav_pcm16_mono_float32(ruta_wake)
            except Exception as exc:
                print(f"Aviso: no se pudo cargar audio de wake {ruta_wake}: {exc}")
        else:
            print(f"Aviso: no existe el audio de wake en {ruta_wake}.")

    def callback(indata: np.ndarray, frames: int, _t, status: object) -> None:
        """PortAudio: convierte cada bloque a ventanas OWW y las encola para inferencia."""
        del frames
        if status:
            pass
        for pcm in buf.extraer_bloques_pcm16(indata):
            _encolar_pcm_oww(pcm)

    stream = sd.InputStream(
        samplerate=int(tasa_efectiva),
        blocksize=blocksize if blocksize is not None and blocksize > 0 else None,
        device=dispositivo,
        channels=int(canales),
        dtype="float32",
        latency="low",
        callback=callback,
    )

    # --- Bucle principal: esperar la primera activación dentro del timeout ---
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
        # Apagar hilo de inferencia de forma ordenada (sentinela None en la cola).
        stop_infer.set()
        while True:
            try:
                infer_queue.put_nowait(None)
                break
            except queue.Full:
                try:
                    infer_queue.get_nowait()
                except queue.Empty:
                    break
        infer_thread.join(timeout=5.0)

    if resultado is None:
        return None
    clave, score = resultado
    # Beep de confirmación **antes** de que el pipeline abra otra grabación.
    if wake_audio is not None:
        audio, sr = wake_audio
        sd.play(audio, samplerate=sr, blocking=True)
    return (clave, score)
