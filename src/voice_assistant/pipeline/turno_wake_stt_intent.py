"""
Un turno completo: esperar wake → (opcional) beep → silencio → grabar → STT → intención → acción.
"""

from __future__ import annotations

import time

from voice_assistant.audio.capture import grabar_muestras, guardar_wav_mono
from voice_assistant.audio.formato_pipeline import preparar_muestras_para_stt
from voice_assistant.intents import cargar_catalogo, emparejar_intencion, ejecutar_accion
from voice_assistant.stt import transcribir_float32_16khz
from voice_assistant.wake import esperar_primera_activacion_wake


def ejecutar_turno_wake_grabar_stt_intent(
    *,
    dispositivo_entrada: int | None,
    timeout_espera_wake_seg: float,
    silencio_post_wake_seg: float,
    duracion_grabacion_orden_seg: float,
    tasa_muestreo_hz: int,
    canales: int,
    modelos_wake: list[str],
    frase_activacion: str,
    umbral_wake: float,
    rebote_wake_seg: float,
    inferencia_wake: str,
    vad_umbral_wake: float,
    blocksize_wake: int | None,
    ruta_audio_confirmacion_wake: str | None,
    catalogo_intenciones_ruta: str,
    whisper_modelo: str,
    whisper_dispositivo: str,
    whisper_tipo_computo: str,
    whisper_idioma: str,
    tasa_salida_pipeline_hz: int,
    carpeta_grabaciones: str,
    guardar_wav_debug: bool,
) -> None:
    """
    Espera una activación de wake, graba la orden, transcribe y ejecuta la intención.
    """
    print(f"Esperando wake word (máx {timeout_espera_wake_seg:.0f} s)...")
    hit_wake = esperar_primera_activacion_wake(
        timeout_espera_wake_seg,
        dispositivo=dispositivo_entrada,
        tasa_muestreo_solicitada_hz=tasa_muestreo_hz,
        canales=canales,
        modelos=modelos_wake,
        frase_objetivo_producto=frase_activacion,
        umbral=umbral_wake,
        rebote_segundos=rebote_wake_seg,
        inferencia=inferencia_wake,
        vad_umbral=vad_umbral_wake,
        blocksize=blocksize_wake,
        ruta_audio_wake=ruta_audio_confirmacion_wake,
    )
    if hit_wake is None:
        print("Tiempo agotado sin detectar wake word.")
        return
    modelo_w, score_w = hit_wake
    print(f"Wake detectado: modelo={modelo_w!r} score={score_w:.3f}")

    if silencio_post_wake_seg > 0:
        print(f"Pausa post-wake {silencio_post_wake_seg:.2f} s (reduce solapamiento con el beep)...")
        time.sleep(silencio_post_wake_seg)

    print(f"Grabando orden ({duracion_grabacion_orden_seg:.1f} s)...")
    muestras, tasa_efectiva = grabar_muestras(
        duracion_grabacion_orden_seg,
        dispositivo=dispositivo_entrada,
        tasa_muestreo_hz=tasa_muestreo_hz,
        canales=canales,
    )
    audio_16k, sr_stt = preparar_muestras_para_stt(muestras, tasa_efectiva, tasa_salida_pipeline_hz)
    if guardar_wav_debug:
        from datetime import datetime
        from pathlib import Path

        carpeta = Path(carpeta_grabaciones)
        carpeta.mkdir(parents=True, exist_ok=True)
        w = carpeta / datetime.now().strftime("turno_orden_%Y%m%d_%H%M%S.wav")
        guardar_wav_mono(w, audio_16k, sr_stt)
        print(f"WAV de depuración: {w.resolve()}")

    print("Transcribiendo (Whisper local)...")
    texto = transcribir_float32_16khz(
        audio_16k,
        modelo=whisper_modelo,
        dispositivo=whisper_dispositivo,
        tipo_computo=whisper_tipo_computo,
        idioma=whisper_idioma,
    )
    print(f"STT: {texto!r}")

    cat = cargar_catalogo(catalogo_intenciones_ruta)
    hit = emparejar_intencion(cat, texto)
    if hit is None:
        print("Sin intención coincidente en el catálogo.")
        return
    print(
        f"Intención: {hit.intencion_id} ({hit.intencion_titulo}) "
        f"disparador={hit.disparador!r} | tras_wake={hit.texto_tras_wake!r}"
    )
    ejecutar_accion(hit.accion, bloqueante=True)
