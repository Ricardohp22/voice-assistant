"""
Escucha continua por bloques (chunks) sin wake word — comando ``--stream-chunks``.

Propósito: herramienta de diagnóstico y medición de rendimiento del micrófono.
Abre un InputStream de PortAudio, recibe audio en callbacks y **no retiene**
buffers de audio (solo contadores y métricas), permitiendo correr largos periodos
sin crecer en memoria por acumulación de muestras.

Este módulo es independiente del pipeline de producción (wake → STT → intenciones).
Solo lo usa ``main.py --stream-chunks``.
"""

from __future__ import annotations

import threading
import time

import numpy as np
import sounddevice as sd

from voice_assistant.audio import resolver_tasa_muestreo_entrada


# =============================================================================
# ACUMULADOR DE MÉTRICAS (thread-safe)
#
# El callback de PortAudio corre en un hilo de audio con prioridad alta.
# _AcumuladorCallback protege los contadores con un Lock para que el hilo
# principal pueda leerlos sin carreras de datos.
# =============================================================================

class _AcumuladorCallback:
    """Estado actualizado solo desde el callback de audio (con candado)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.chunks: int = 0
        self.marcos: int = 0
        self.callbacks_con_estado: int = 0
        self._prev_mono: float | None = None
        self.intervalo_callback_ms_max: float = 0.0

    def registrar(self, frames: int, status: object) -> None:
        ahora = time.monotonic()
        with self._lock:
            self.chunks += 1
            self.marcos += int(frames)
            if status:
                self.callbacks_con_estado += 1
            if self._prev_mono is not None:
                dt_ms = (ahora - self._prev_mono) * 1000.0
                if dt_ms > self.intervalo_callback_ms_max:
                    self.intervalo_callback_ms_max = dt_ms
            self._prev_mono = ahora

    def leer_totales_y_reset_pico_intervalo(self) -> tuple[int, int, int, float]:
        """Devuelve (chunks, marcos, callbacks_con_estado, pico_intervalo_ms) y anula el pico."""
        with self._lock:
            pico = self.intervalo_callback_ms_max
            self.intervalo_callback_ms_max = 0.0
            return self.chunks, self.marcos, self.callbacks_con_estado, pico


def _leer_vm_rss_mib() -> float | None:
    """Resident set size del proceso en MiB (Linux); None si no aplica."""
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    partes = line.split()
                    return float(partes[1]) / 1024.0     # kB → MiB
    except OSError:
        return None


# =============================================================================
# FUNCIÓN PRINCIPAL
# =============================================================================

def ejecutar_escucha_continua(
    duracion_segundos: float,
    *,
    dispositivo: int | None,
    tasa_muestreo_solicitada_hz: int,
    canales: int,
    marcos_por_bloque: int,
    latencia: str | float,
    estadisticas_cada_seg: float = 1.0,
) -> None:
    """
    Captura en bloques durante ``duracion_segundos`` (0 = hasta ``KeyboardInterrupt``).

    Cada ``estadisticas_cada_seg`` segundos imprime una línea con:
    - Chunks y marcos acumulados y su tasa instantánea.
    - Deriva entre tiempo de reloj y audio equivalente capturado.
    - Pico de intervalo entre callbacks (detecta jitter / xruns).
    - Uso de CPU del proceso y RSS de memoria.

    Args:
        duracion_segundos: 0 = no termina por tiempo (use Ctrl+C).
        dispositivo: Índice PortAudio o None.
        tasa_muestreo_solicitada_hz: Tasa deseada; si el hw no la admite, se usa la nativa.
        canales: Canales de entrada.
        marcos_por_bloque: blocksize del InputStream (muestras por callback).
        latencia: "low", "high" o segundos (sounddevice).
        estadisticas_cada_seg: Cada cuántos segundos imprimir métricas.
    """
    if marcos_por_bloque <= 0:
        raise ValueError("marcos_por_bloque debe ser > 0")
    if estadisticas_cada_seg <= 0:
        raise ValueError("estadisticas_cada_seg debe ser > 0")

    # Verificar la tasa efectiva antes de abrir el stream, para advertir si habrá fallback.
    tasa_efectiva = resolver_tasa_muestreo_entrada(
        dispositivo,
        tasa_muestreo_solicitada_hz,
        canales=canales,
    )
    if tasa_efectiva != tasa_muestreo_solicitada_hz:
        print(
            f"Aviso: {tasa_muestreo_solicitada_hz} Hz no soportado; "
            f"stream a {tasa_efectiva} Hz (nativo/predeterminado del dispositivo)."
        )

    acum = _AcumuladorCallback()
    esperado_ms = (marcos_por_bloque / float(tasa_efectiva)) * 1000.0

    def callback(_indata: np.ndarray, frames: int, _t, status: object) -> None:
        acum.registrar(frames, status)

    stream = sd.InputStream(
        samplerate=tasa_efectiva,
        blocksize=marcos_por_bloque,
        device=dispositivo,
        channels=canales,
        dtype="float32",
        latency=latencia,
        callback=callback,
    )

    t0_wall = time.perf_counter()
    t0_cpu = time.process_time()
    ultimo_informe = t0_wall
    c_prev, m_prev = 0, 0

    print(
        f"Stream: {tasa_efectiva} Hz, blocksize={marcos_por_bloque} "
        f"(~{esperado_ms:.2f} ms/callback), latencia={latencia!r}. "
        "Ctrl+C para detener antes de tiempo."
    )

    with stream:
        stream.start()
        try:
            while True:
                time.sleep(min(0.05, estadisticas_cada_seg * 0.25))
                ahora = time.perf_counter()
                if duracion_segundos > 0 and (ahora - t0_wall) >= duracion_segundos:
                    break
                if (ahora - ultimo_informe) < estadisticas_cada_seg:
                    continue

                dt_informe = max(ahora - ultimo_informe, 1e-9)
                ultimo_informe = ahora

                c1, m1, x1, pico_ms = acum.leer_totales_y_reset_pico_intervalo()
                d_chunks = c1 - c_prev
                d_marcos = m1 - m_prev
                c_prev, m_prev = c1, m1

                wall = ahora - t0_wall
                cpu = time.process_time() - t0_cpu
                audio_s = m1 / float(tasa_efectiva)
                deriva_ms = (wall - audio_s) * 1000.0
                rss = _leer_vm_rss_mib()

                chunks_por_seg = d_chunks / dt_informe
                marcos_por_seg = d_marcos / dt_informe

                partes = [
                    f"t_wall={wall:6.1f}s",
                    f"chunks={c1} (+{d_chunks} en {dt_informe:.2f}s → {chunks_por_seg:.0f}/s)",
                    f"marcos/s≈{marcos_por_seg:5.0f}",
                    f"audio_eq={audio_s:6.2f}s",
                    f"deriva_ms={deriva_ms:+7.1f}",
                    f"pico_intervalo_ms={pico_ms:6.1f} (esperado ~{esperado_ms:.1f})",
                    f"xruns_flags={x1}",
                    f"cpu_proc={cpu:5.2f}s ({100.0 * cpu / max(wall, 1e-9):4.1f}%·1 núcleo)",
                ]
                if rss is not None:
                    partes.append(f"RSS≈{rss:.1f} MiB")
                print("  " + " | ".join(partes))

        except KeyboardInterrupt:
            print("\nInterrupción por teclado; cerrando stream…")
        finally:
            stream.stop()

    wall_fin = time.perf_counter() - t0_wall
    c_fin, m_fin, x_fin, _ = acum.leer_totales_y_reset_pico_intervalo()
    cpu_fin = time.process_time() - t0_cpu
    print(
        f"Resumen: {c_fin} callbacks, {m_fin} marcos "
        f"(~{m_fin / tasa_efectiva:.2f} s de audio eq.), "
        f"{x_fin} callbacks con flag de estado, "
        f"{wall_fin:.2f} s reloj, {cpu_fin:.2f} s CPU proceso."
    )
