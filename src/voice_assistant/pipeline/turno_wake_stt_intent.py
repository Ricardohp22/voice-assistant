"""
Pipeline de un turno completo (``--wake-turn``).

Secuencia:
  1. ``esperar_primera_activacion_wake`` — stream + openWakeWord hasta la primera detección.
  2. Pausa opcional post-wake (evitar solapamiento con el beep de confirmación).
  3. ``grabar_muestras`` — ventana fija para la orden del usuario.
  4. ``preparar_muestras_para_stt`` — mono 16 kHz para Whisper.
  5. ``transcribir_float32_16khz`` — texto de la orden.
  6. ``emparejar_intencion`` + ``ejecutar_accion`` — catálogo JSON local.

Ver también ``docs/wake_turn.md``.
"""

from __future__ import annotations

import time

from voice_assistant.audio.capture import grabar_muestras, guardar_wav_mono
from voice_assistant.audio.formato_pipeline import preparar_muestras_para_stt
from voice_assistant.intents import cargar_catalogo, emparejar_intencion, ejecutar_accion
from voice_assistant.stt import transcribir_float32_16khz
from voice_assistant.config_theme import COLOR
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
    Ejecuta un único ciclo wake → orden → STT → intención → acción.

    Pensado para pruebas manuales (``python main.py --wake-turn``) y como
    plantilla para un bucle continuo en producción. Cada fase imprime estado
    en consola; los fallos terminan el turno sin excepción salvo errores graves
    (micrófono, JSON inválido, acción mal definida).
    """
    # -------------------------------------------------------------------------
    # Fase 1: esperar la primera activación de wake word (openWakeWord en stream)
    # -------------------------------------------------------------------------
    # El micrófono queda abierto en ``esperar_primera_activacion_wake``; al detectar
    # wake puede reproducirse un WAV de confirmación (bloqueante) antes de devolver.
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

    # -------------------------------------------------------------------------
    # Fase 2: pausa breve para que el beep / cola de wake no entre en la grabación
    # -------------------------------------------------------------------------
    if silencio_post_wake_seg > 0:
        print(f"Pausa post-wake {silencio_post_wake_seg:.2f} s (reduce solapamiento con el beep)...")
        time.sleep(silencio_post_wake_seg)

    # -------------------------------------------------------------------------
    # Fase 3: grabación bloqueante de la orden del usuario
    # -------------------------------------------------------------------------
    # ``grabar_muestras`` abre de nuevo el micrófono (stream distinto al del wake).
    print(f"{COLOR['rojo']}Grabando orden ({duracion_grabacion_orden_seg:.1f} s)...{COLOR['reset']}")
    muestras, tasa_efectiva = grabar_muestras(
        duracion_grabacion_orden_seg,
        dispositivo=dispositivo_entrada,
        tasa_muestreo_hz=tasa_muestreo_hz,
        canales=canales,
    )

    # -------------------------------------------------------------------------
    # Fase 4: normalizar audio al contrato STT (mono float32 @ 16 kHz habitualmente)
    # -------------------------------------------------------------------------
    audio_16k, sr_stt = preparar_muestras_para_stt(muestras, tasa_efectiva, tasa_salida_pipeline_hz)

    # WAV opcional para depurar si Whisper o el micrófono fallan en campo.
    if guardar_wav_debug:
        from datetime import datetime
        from pathlib import Path

        carpeta = Path(carpeta_grabaciones)
        carpeta.mkdir(parents=True, exist_ok=True)
        w = carpeta / datetime.now().strftime("turno_orden_%Y%m%d_%H%M%S.wav")
        guardar_wav_mono(w, audio_16k, sr_stt)
        print(f"WAV de depuración: {w.resolve()}")

    # -------------------------------------------------------------------------
    # Fase 5: transcripción local (faster-whisper)
    # -------------------------------------------------------------------------
    print("Transcribiendo (Whisper local)...")
    texto = transcribir_float32_16khz(
        audio_16k,
        modelo=whisper_modelo,
        dispositivo=whisper_dispositivo,
        tipo_computo=whisper_tipo_computo,
        idioma=whisper_idioma,
    )
    print(f"STT: {texto!r}")

    # -------------------------------------------------------------------------
    # Fase 6: emparejar contra el catálogo y ejecutar la acción asociada
    # -------------------------------------------------------------------------
    cat = cargar_catalogo(catalogo_intenciones_ruta)
    hit = emparejar_intencion(cat, texto)
    if hit is None:
        print("Sin intención coincidente en el catálogo.")
        return
    print(
        f"Intención: {hit.intencion_id} ({hit.intencion_titulo}) "
        f"disparador={hit.disparador!r} | tras_wake={hit.texto_tras_wake!r}"
    )
    # ``bloqueante=True`` evita que el proceso termine antes de oír el WAV de respuesta.
    ejecutar_accion(hit.accion, bloqueante=True)
