"""
Captura de audio desde el micrófono y guardado en WAV (PCM 16 bits, mono).

Depende de `sounddevice` (PortAudio). El índice de entrada suele resolverse desde
`config` vía ``audio.dispositivo.resolver_dispositivo_entrada`` (índice o nombre).
"""

from __future__ import annotations

import wave
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd

# Código de error PortAudio cuando la tasa de muestreo no es válida para el dispositivo.
_PA_ERROR_INVALID_SAMPLE_RATE = -9997


def _info_entrada(dispositivo: int | None) -> dict[str, Any]:
    """
    Devuelve el dict de sounddevice para el dispositivo de entrada indicado.

    Si `dispositivo` es None, consulta la entrada predeterminada del sistema.
    """
    return sd.query_devices(dispositivo, kind="input")


def _tasa_predeterminada_dispositivo(dispositivo: int | None) -> int:
    """Tasa por defecto reportada por PortAudio para ese dispositivo (suele ser la nativa)."""
    info = _info_entrada(dispositivo)
    return int(round(float(info["default_samplerate"])))


def _es_error_tasa_invalida(exc: BaseException) -> bool:
    """True si la excepción corresponde a tasa de muestreo no soportada por el hardware."""
    if isinstance(exc, sd.PortAudioError):
        if len(exc.args) >= 2 and exc.args[1] == _PA_ERROR_INVALID_SAMPLE_RATE:
            return True
        if "Invalid sample rate" in str(exc):
            return True
    return False


def resolver_tasa_muestreo_entrada(
    dispositivo: int | None,
    tasa_solicitada: int,
    *,
    canales: int = 1,
) -> int:
    """
    Devuelve la tasa de muestreo que PortAudio usará al abrir la entrada.

    Usado al **iniciar** el stream de wake (``esperar_primera_activacion_wake``)
    para saber si habrá que remuestrear a 16 kHz antes de openWakeWord.

    Si ``tasa_solicitada`` no es válida para el dispositivo, devuelve la tasa
    predeterminada del hardware (misma lógica que ``grabar_muestras``).
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


def listar_dispositivos_entrada(imprimir: bool = True) -> list[dict[str, Any]]:
    """
    Devuelve la lista de dispositivos de entrada que expone PortAudio.

    Cada elemento es un dict con al menos: índice, nombre, canales de entrada
    (`max_input_channels`), tasa por defecto, etc. Si `imprimir` es True, muestra
    un resumen legible en consola para copiar el índice al `config.py`.

    Returns:
        Lista de dispositivos (solo los que admiten entrada, max_input_channels > 0).
    """
    dispositivos: list[dict[str, Any]] = []
    for i, dev in enumerate(sd.query_devices()):
        if int(dev.get("max_input_channels", 0) or 0) > 0:
            entrada = dict(dev)
            entrada["_indice"] = i
            dispositivos.append(entrada)

    if imprimir:
        predeterminado = sd.default.device[0]  # índice de entrada por defecto
        print("Dispositivos de ENTRADA (micrófono). El predeterminado del sistema está marcado con *")
        for d in dispositivos:
            idx = d["_indice"]
            marca = "*" if idx == predeterminado else " "
            nombre = d.get("name", "?")
            ch = d.get("max_input_channels", "?")
            hz = d.get("default_samplerate", "?")
            print(f"  {marca} [{idx}] {nombre}  (in_max={ch}, default_sr={hz})")

    return dispositivos


def grabar_muestras(
    duracion_segundos: float,
    *,
    dispositivo: int | None = None,
    tasa_muestreo_hz: int = 16_000,
    canales: int = 1,
) -> tuple[np.ndarray, int]:
    """
    Graba la **orden del usuario** tras el wake (fase 3 de ``--wake-turn``).

    Grabación **bloqueante** con ``sd.rec``: no usa callbacks. El pipeline pasa
    el resultado a ``preparar_muestras_para_stt`` para Whisper.

    Las muestras son NumPy float32 en [-1.0, 1.0]. La tasa efectiva puede
    diferir de la solicitada (USB a 48 kHz, etc.); en ese caso se reintenta con
    la tasa nativa del dispositivo.

    Args:
        duracion_segundos: Tiempo de grabación (> 0); ver ``POST_WAKE_GRABAR_ORDEN_SEG``.
        dispositivo: Índice PortAudio o None para el predeterminado.
        tasa_muestreo_hz: Frecuencia deseada; si falla, se prueba la nativa del dispositivo.
        canales: Número de canales de entrada (1 = mono).

    Returns:
        Tupla (array (frames, canales) float32, tasa real en Hz).

    Raises:
        ValueError: Si la duración no es positiva.
        sounddevice.PortAudioError: Si no se puede abrir el flujo ni con la tasa alternativa.
    """
    if duracion_segundos <= 0:
        raise ValueError("duracion_segundos debe ser > 0")

    def _rec(tasa_hz: int) -> np.ndarray:
        num_frames = int(round(duracion_segundos * tasa_hz))
        # Lectura bloqueante: el hilo queda aquí hasta completar la ventana de la orden.
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
        # Aviso útil al depurar hardware “duro” frente a rutas con remuestreo.
        print(
            f"Aviso: {tasa_muestreo_hz} Hz no soportado en este dispositivo; "
            f"grabando a {tasa_usada} Hz (tasa predeterminada PortAudio)."
        )

    return np.asarray(muestras, dtype=np.float32), tasa_usada


def guardar_wav_mono(
    ruta_salida: str | Path,
    muestras: np.ndarray,
    tasa_muestreo_hz: int,
) -> Path:
    """
    Guarda audio en WAV PCM 16-bit mono (depuración o pruebas).

    En ``--wake-turn`` solo se usa si ``WAKE_TURN_GUARDAR_WAV_DEBUG`` es True,
    para revisar qué captó el micrófono antes de Whisper.

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
        # Mezcla simple a mono para revisar la captura en un solo canal.
        audio = np.mean(audio, axis=1)
    elif audio.ndim == 2 and audio.shape[1] == 1:
        audio = audio[:, 0]

    audio = np.clip(audio, -1.0, 1.0)
    pcm16 = (audio * 32767.0).astype(np.int16)

    with wave.open(str(ruta), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16 bits = 2 bytes
        wf.setframerate(int(tasa_muestreo_hz))
        wf.writeframes(pcm16.tobytes())

    return ruta.resolve()
