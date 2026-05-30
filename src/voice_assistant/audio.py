"""
Primitivas de audio del asistente de voz.

Este módulo agrupa todo lo necesario para capturar, convertir y guardar audio.
Es la base sobre la que se apoyan wake.py, pipeline.py e intents.py.

Secciones:
  1. RESOLUCIÓN DE DISPOSITIVO  — buscar el micrófono por nombre o índice.
  2. CAPTURA                    — grabar muestras bloqueante con sd.rec.
  3. FORMATO / PIPELINE         — mono float32, remuestreo lineal, contrato STT.
  4. WAV I/O                    — guardar y cargar archivos WAV PCM 16-bit.

Dependencias externas: numpy, sounddevice (PortAudio).
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd

# Código de error PortAudio cuando la tasa de muestreo no es válida para el hw.
_PA_ERROR_INVALID_SAMPLE_RATE = -9997


# =============================================================================
# 1. RESOLUCIÓN DE DISPOSITIVO
#
# El micrófono puede identificarse por nombre (p. ej. "compartido" para el PCM
# dsnoop de ALSA) o por índice PortAudio. El nombre tiene prioridad porque
# sobrevive a reordenamientos de hardware entre reinicios.
# =============================================================================

def resolver_dispositivo_entrada(
    nombre_contiene: str | None,
    indice: int | None,
) -> int | None:
    """
    Devuelve el índice PortAudio a pasar a sounddevice como ``device``.

    Prioridad:
        1. Si ``nombre_contiene`` es una cadena no vacía, el primer dispositivo
           de entrada cuyo nombre la contiene (sin distinguir mayúsculas).
        2. Si no, ``indice`` tal cual (None = entrada predeterminada del sistema).
    """
    if nombre_contiene is not None and nombre_contiene.strip():
        return _buscar_indice_por_subcadena(nombre_contiene.strip())
    return indice


def resolver_dispositivo_entrada_config() -> int | None:
    """Atajo: lee ``MIC_NOMBRE_CONTIENE`` y ``DISPOSITIVO_ENTRADA`` de config."""
    from voice_assistant import config

    return resolver_dispositivo_entrada(
        config.MIC_NOMBRE_CONTIENE,
        config.DISPOSITIVO_ENTRADA,
    )


def describir_dispositivo_entrada(dispositivo: int | None) -> str:
    """Nombre legible del dispositivo (o 'predeterminado del sistema')."""
    if dispositivo is None:
        return "predeterminado del sistema"
    try:
        info = sd.query_devices(dispositivo, kind="input")
        return f"[{dispositivo}] {info.get('name', '?')}"
    except Exception:
        return f"[{dispositivo}] (no consultable)"


def listar_dispositivos_entrada(imprimir: bool = True) -> list[dict[str, Any]]:
    """
    Devuelve los dispositivos de entrada que expone PortAudio.

    Si ``imprimir`` es True, muestra un resumen legible para copiar el índice
    al config.py. Cada elemento del resultado incluye ``_indice`` (int).
    """
    dispositivos: list[dict[str, Any]] = []
    for i, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0) or 0) > 0:
            entrada = dict(dev)
            entrada["_indice"] = i
            dispositivos.append(entrada)

    if imprimir:
        predeterminado = sd.default.device[0]
        print("Dispositivos de ENTRADA (micrófono). El predeterminado del sistema está marcado con *")
        for d in dispositivos:
            idx = d["_indice"]
            marca = "*" if idx == predeterminado else " "
            print(
                f"  {marca} [{idx}] {d.get('name', '?')}  "
                f"(in_max={d.get('max_input_channels', '?')}, "
                f"default_sr={d.get('default_samplerate', '?')})"
            )

    return dispositivos


def comprobar_entrada_entrega_muestras(
    dispositivo: int | None,
    *,
    tasa_muestreo_hz: int,
    canales: int,
    duracion_segundos: float = 0.12,
) -> tuple[int, float]:
    """
    Graba un instante mínimo y verifica que las muestras son finitas y no vacías.

    Returns:
        (tasa_efectiva_hz, rms aproximado en float32 [-1, 1]).

    Raises:
        ValueError: Si las muestras contienen NaN/inf o están vacías.
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


def _buscar_indice_por_subcadena(subcadena: str) -> int:
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
            f"Varios dispositivos coinciden con {subcadena!r}; "
            f"acorte la cadena o use el índice:\n{lineas}"
        )
    return coincidencias[0][0]


# =============================================================================
# 2. CAPTURA
#
# ``grabar_muestras`` es la función de grabación bloqueante usada en dos sitios:
#   - pipeline.py → para capturar la orden del usuario tras el wake.
#   - intents.py  → para capturar el nombre de la reunión (segunda escucha).
#
# ``resolver_tasa_muestreo_entrada`` la usa wake.py para saber de antemano si
# el mic requiere remuestreo antes de alimentar a openWakeWord (que espera 16 kHz).
# =============================================================================

def resolver_tasa_muestreo_entrada(
    dispositivo: int | None,
    tasa_solicitada: int,
    *,
    canales: int = 1,
) -> int:
    """
    Devuelve la tasa efectiva que PortAudio usará al abrir la entrada.

    Si ``tasa_solicitada`` no es válida para el dispositivo, devuelve la tasa
    predeterminada del hardware (misma lógica de fallback que ``grabar_muestras``).
    """
    try:
        sd.check_input_settings(
            device=dispositivo,
            samplerate=int(tasa_solicitada),
            channels=int(canales),
            dtype="float32",
        )
        return int(tasa_solicitada)
    except sd.PortAudioError as exc:
        if not _es_error_tasa_invalida(exc):
            raise
        alterna = _tasa_predeterminada_dispositivo(dispositivo)
        if alterna == tasa_solicitada:
            raise
        return alterna


def grabar_muestras(
    duracion_segundos: float,
    *,
    dispositivo: int | None = None,
    tasa_muestreo_hz: int = 16_000,
    canales: int = 1,
) -> tuple[np.ndarray, int]:
    """
    Graba ``duracion_segundos`` de audio de forma **bloqueante** (sd.rec).

    El resultado es float32 en [-1.0, 1.0]. La tasa efectiva puede diferir de
    la solicitada si el hardware USB no acepta 16 kHz; en ese caso se reintenta
    automáticamente con la tasa nativa del dispositivo.

    Args:
        duracion_segundos: Tiempo de grabación (> 0).
        dispositivo: Índice PortAudio o None para el predeterminado.
        tasa_muestreo_hz: Frecuencia deseada; si falla, se usa la nativa.
        canales: Número de canales de entrada (1 = mono).

    Returns:
        (array float32 (frames, canales), tasa real en Hz).

    Raises:
        ValueError: Si la duración no es positiva.
        sounddevice.PortAudioError: Si no se puede abrir el flujo.
    """
    if duracion_segundos <= 0:
        raise ValueError("duracion_segundos debe ser > 0")

    def _rec(tasa_hz: int) -> np.ndarray:
        num_frames = int(round(duracion_segundos * tasa_hz))
        return sd.rec(
            num_frames,
            samplerate=tasa_hz,
            channels=canales,
            dtype="float32",
            device=dispositivo,
            blocking=True,
        )

    tasa_usada = tasa_muestreo_hz
    try:
        muestras = _rec(tasa_usada)
    except sd.PortAudioError as exc:
        if not _es_error_tasa_invalida(exc):
            raise
        alterna = _tasa_predeterminada_dispositivo(dispositivo)
        if alterna == tasa_muestreo_hz:
            raise
        tasa_usada = alterna
        muestras = _rec(tasa_usada)
        print(
            f"Aviso: {tasa_muestreo_hz} Hz no soportado en este dispositivo; "
            f"grabando a {tasa_usada} Hz (tasa predeterminada PortAudio)."
        )

    return np.asarray(muestras, dtype=np.float32), tasa_usada


def _tasa_predeterminada_dispositivo(dispositivo: int | None) -> int:
    """Tasa por defecto reportada por PortAudio (suele ser la nativa del hw)."""
    info = sd.query_devices(dispositivo, kind="input")
    return int(round(float(info["default_samplerate"])))


def _es_error_tasa_invalida(exc: BaseException) -> bool:
    """True si la excepción es de tasa de muestreo no soportada por el hw."""
    if isinstance(exc, sd.PortAudioError):
        if len(exc.args) >= 2 and exc.args[1] == _PA_ERROR_INVALID_SAMPLE_RATE:
            return True
        if "Invalid sample rate" in str(exc):
            return True
    return False


# =============================================================================
# 3. FORMATO / PIPELINE
#
# Contrato de salida hacia STT: array float32 mono 1D en [-1, 1] a 16 kHz.
# ``preparar_muestras_para_stt`` es el puente entre ``grabar_muestras`` y
# ``transcribir_float32_16khz``; aplica los dos pasos en secuencia.
#
# ``mono_float32`` y ``remuestrear_mono_lineal`` también los usa wake.py
# internamente para preparar ventanas de audio antes de openWakeWord.
# =============================================================================

def mono_float32(muestras: np.ndarray) -> np.ndarray:
    """
    Convierte (frames,) o (frames, C) float32 a un solo canal 1D.

    Varios canales se promedian; un canal se aplana. Resultado en [-1, 1].
    """
    audio = np.asarray(muestras, dtype=np.float32)
    if audio.ndim == 2:
        if audio.shape[1] > 1:
            audio = np.mean(audio, axis=1, dtype=np.float32)
        else:
            audio = audio[:, 0]
    elif audio.ndim != 1:
        raise ValueError("muestras debe ser 1D o 2D (frames, canales)")
    return np.clip(audio, -1.0, 1.0).astype(np.float32, copy=False)


def remuestrear_mono_lineal(
    mono: np.ndarray,
    tasa_entrada_hz: int,
    tasa_salida_hz: int,
) -> np.ndarray:
    """
    Remuestreo por interpolación lineal (sin dependencias extra; suficiente para voz).

    Si las tasas coinciden, devuelve el mismo contenido sin recalcular.
    """
    if tasa_entrada_hz <= 0 or tasa_salida_hz <= 0:
        raise ValueError("Las tasas de muestreo deben ser > 0")
    x = np.asarray(mono, dtype=np.float32).reshape(-1)
    if x.size == 0:
        return x.copy()
    if tasa_entrada_hz == tasa_salida_hz:
        return x.astype(np.float32, copy=False)

    n_out = max(1, int(round(x.size * tasa_salida_hz / tasa_entrada_hz)))
    t_in = np.linspace(0.0, (x.size - 1) / tasa_entrada_hz, num=x.size, dtype=np.float64)
    t_out = np.linspace(0.0, (n_out - 1) / tasa_salida_hz, num=n_out, dtype=np.float64)
    y = np.interp(t_out, t_in, x.astype(np.float64))
    return np.clip(y, -1.0, 1.0).astype(np.float32)


def preparar_muestras_para_stt(
    muestras: np.ndarray,
    tasa_grabacion_hz: int,
    tasa_objetivo_hz: int,
) -> tuple[np.ndarray, int]:
    """
    Convierte la grabación cruda al contrato de Whisper: mono float32 @ 16 kHz.

    Pasos en secuencia:
        1. ``mono_float32``         → mezcla canales y asegura float32 en [-1, 1].
        2. ``remuestrear_mono_lineal`` → ajusta la tasa si el mic grabó a otra frecuencia.

    Args:
        muestras: float32 tal como devuelve ``grabar_muestras``.
        tasa_grabacion_hz: Tasa real de la grabación (p. ej. 48 000 en USB).
        tasa_objetivo_hz: Tasa para STT (config.TASA_SALIDA_PIPELINE_HZ).

    Returns:
        (audio_mono_float32, tasa_objetivo_hz).
    """
    mono = mono_float32(muestras)
    out = remuestrear_mono_lineal(mono, tasa_grabacion_hz, tasa_objetivo_hz)
    return out, int(tasa_objetivo_hz)


# =============================================================================
# 4. WAV I/O
#
# ``guardar_wav_mono`` se usa en main.py (--test-record) y en pipeline.py
# cuando WAKE_TURN_GUARDAR_WAV_DEBUG está activado.
# ``cargar_wav_pcm16_mono_float32`` la usa wake.py para cargar el beep de
# confirmación y intents.py para reproducir los audios de respuesta.
# =============================================================================

def guardar_wav_mono(
    ruta_salida: str | Path,
    muestras: np.ndarray,
    tasa_muestreo_hz: int,
) -> Path:
    """
    Guarda audio en WAV PCM 16-bit mono.

    Args:
        ruta_salida: Ruta del .wav a crear (se crean directorios padre si no existen).
        muestras: Array (frames,) o (frames, canales) float32.
        tasa_muestreo_hz: Frecuencia de muestreo del WAV.

    Returns:
        Path absoluto/normalizado del archivo escrito.
    """
    ruta = Path(ruta_salida)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    audio = np.asarray(muestras, dtype=np.float32)
    if audio.ndim == 2 and audio.shape[1] > 1:
        audio = np.mean(audio, axis=1)
    elif audio.ndim == 2 and audio.shape[1] == 1:
        audio = audio[:, 0]

    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767.0).astype(np.int16)

    with wave.open(str(ruta), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)      # 16 bits = 2 bytes
        wf.setframerate(int(tasa_muestreo_hz))
        wf.writeframes(pcm16.tobytes())

    return ruta.resolve()


def cargar_wav_pcm16_mono_float32(ruta: str | Path) -> tuple[np.ndarray, int]:
    """
    Carga WAV PCM 16-bit; si hay varios canales, promedia a mono.

    Returns:
        (audio float32 en [-1, 1], tasa_hz).

    Raises:
        ValueError: Si el archivo no es PCM 16-bit.
    """
    p = Path(ruta)
    with wave.open(str(p), "rb") as wf:
        canales = int(wf.getnchannels())
        tasa = int(wf.getframerate())
        ancho = int(wf.getsampwidth())
        if ancho != 2:
            raise ValueError(f"Se esperaba PCM 16-bit en {p}, sampwidth={ancho}")
        data = wf.readframes(int(wf.getnframes()))
    arr = np.frombuffer(data, dtype=np.int16)
    if canales > 1:
        arr = arr.reshape(-1, canales).mean(axis=1).astype(np.int16, copy=False)
    audio = (arr.astype(np.float32) / 32767.0).reshape(-1)
    return np.clip(audio, -1.0, 1.0), tasa
