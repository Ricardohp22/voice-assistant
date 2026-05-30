"""
Pipeline de un turno completo: wake → grabar orden → STT → intención → acción.

Este módulo es el director de orquesta del asistente. Cada fase tiene una
responsabilidad clara y delega en el módulo especializado correspondiente:

  Fase 1 — WAKE      : wake.esperar_primera_activacion_wake
                         Escucha OWW; devuelve al primer hit o al timeout.
  Fase 2 — PAUSA     : time.sleep (opcional)
                         Evita que el beep de confirmación entre en la grabación.
  Fase 3 — GRABAR    : audio.grabar_muestras
                         Ventana fija bloqueante para la orden del usuario.
  Fase 4 — FORMATO   : audio.preparar_muestras_para_stt
                         Mono float32 @ 16 kHz (contrato Whisper).
  Fase 5 — STT       : stt.transcribir_float32_16khz
                         Texto de la orden vía faster-whisper local.
  Fase 6 — INTENCIÓN : intents.emparejar_intencion + intents.ejecutar_intencion
                         Catálogo JSON → manejador en código.

Ver también docs/wake_turn.md y docs/funciones_post_wakeup.md.
"""

from __future__ import annotations

import time

from voice_assistant import config
from voice_assistant.audio import grabar_muestras, guardar_wav_mono, preparar_muestras_para_stt
from voice_assistant.intents import cargar_catalogo, emparejar_intencion, ejecutar_intencion
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
    Ejecuta un único ciclo completo wake → orden → STT → intención → acción.

    Pensado para ``python main.py --wake-turn`` y como plantilla para un bucle
    continuo en producción. Cada fase imprime su estado en consola; los fallos
    terminan el turno sin excepción salvo errores graves (micrófono, JSON
    inválido, intención sin manejador registrado).

    Todos los parámetros se leen de ``config.py`` en ``main.py``; este módulo
    no importa ``config`` directamente para ser testeable con valores inyectados.
    """
    # -------------------------------------------------------------------------
    # Fase 1: Wake word
    # El stream queda abierto dentro de esperar_primera_activacion_wake; al
    # detectar wake reproduce el beep (bloqueante) antes de devolver.
    # -------------------------------------------------------------------------
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
    # Fase 2: Pausa post-wake
    # Deja que el beep de confirmación termine de reproducirse por hardware
    # antes de abrir el stream de grabación.
    # -------------------------------------------------------------------------
    if silencio_post_wake_seg > 0:
        print(f"Pausa post-wake {silencio_post_wake_seg:.2f} s...")
        time.sleep(silencio_post_wake_seg)

    # -------------------------------------------------------------------------
    # Fase 3: Grabación de la orden
    # Abre un stream distinto al de wake (bloqueante, sd.rec).
    # -------------------------------------------------------------------------
    print(f"{config.COLOR['rojo']}Grabando orden ({duracion_grabacion_orden_seg:.1f} s)...{config.COLOR['reset']}")
    muestras, tasa_efectiva = grabar_muestras(
        duracion_grabacion_orden_seg,
        dispositivo=dispositivo_entrada,
        tasa_muestreo_hz=tasa_muestreo_hz,
        canales=canales,
    )

    # -------------------------------------------------------------------------
    # Fase 4: Normalizar al contrato STT (mono float32 @ 16 kHz)
    # -------------------------------------------------------------------------
    audio_16k, sr_stt = preparar_muestras_para_stt(muestras, tasa_efectiva, tasa_salida_pipeline_hz)

    # WAV opcional para revisar en campo qué captó el mic antes de Whisper.
    if guardar_wav_debug:
        from datetime import datetime
        from pathlib import Path

        carpeta = Path(carpeta_grabaciones)
        carpeta.mkdir(parents=True, exist_ok=True)
        w = carpeta / datetime.now().strftime("turno_orden_%Y%m%d_%H%M%S.wav")
        guardar_wav_mono(w, audio_16k, sr_stt)
        print(f"WAV de depuración: {w.resolve()}")

    # -------------------------------------------------------------------------
    # Fase 5: Transcripción STT (faster-whisper local)
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
    # Fase 6: Emparejamiento en catálogo JSON y ejecución del manejador
    # emparejar_intencion busca el disparador más largo con mayor prioridad.
    # ejecutar_intencion despacha al manejador registrado en intents.py.
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
    ejecutar_intencion(hit.intencion_id, bloqueante=True)
