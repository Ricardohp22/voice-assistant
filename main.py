"""
Punto de entrada del asistente de voz — CLI de pruebas y producción.

Instalar el paquete en modo editable (recomendado):

    source venv/bin/activate
    pip install -r requirements.txt
    pip install -e .

Comandos disponibles:

    python main.py --list-devices
    python main.py --check-input-device
    python main.py --test-record 5
    python main.py --test-record 5 --raw-device-rate
    python main.py --stream-chunks 30
    python main.py --stream-chunks 0
    python main.py --wake-listen 60
    python main.py --wake-listen 0
    python main.py --list-intents
    python main.py --test-oracion "Hex vox device hola"
    python main.py --wake-turn

Alternativa sin instalar: ``PYTHONPATH=src python main.py ...``

Comandos ALSA útiles en Linux para ver hardware:

    arecord -l
    arecord -L | grep compartido   # PCM dsnoop compartido (Python + Node)

Micrófono compartido: ver docs/alsa_mic_compartido.md (MIC_NOMBRE_CONTIENE = compartido).

Estructura de módulos (de más bajo a más alto nivel):
    config.py           — parámetros globales ajustables
    audio.py            — primitivas de captura, dispositivo, formato, WAV
    captura_continua.py — escucha continua con métricas (--stream-chunks)
    wake.py             — detección de wake word con openWakeWord
    stt.py              — transcripción con faster-whisper
    intents.py          — catálogo JSON + manejadores de intenciones
    redis_reunion.py    — comunicación Pub/Sub con Node.js
    pipeline.py         — orquestación del turno completo (--wake-turn)
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from voice_assistant import config
from voice_assistant.audio import (
    comprobar_entrada_entrega_muestras,
    describir_dispositivo_entrada,
    grabar_muestras,
    guardar_wav_mono,
    listar_dispositivos_entrada,
    preparar_muestras_para_stt,
    resolver_dispositivo_entrada_config,
)
from voice_assistant.captura_continua import ejecutar_escucha_continua
from voice_assistant.intents import cargar_catalogo, emparejar_intencion, ejecutar_intencion
from voice_assistant.pipeline import ejecutar_turno_wake_grabar_stt_intent
from voice_assistant.wake import ejecutar_escucha_openwakeword


# =============================================================================
# RESOLUCIÓN DE MICRÓFONO
# Función compartida por todos los comandos para usar el mismo criterio de
# selección de micrófono (nombre > índice, ver config.MIC_NOMBRE_CONTIENE).
# =============================================================================

def _dispositivo_entrada_resuelto() -> int | None:
    """
    Índice PortAudio según config (nombre tiene prioridad sobre índice).

    Por defecto busca 'compartido' (dsnoop ALSA) para no bloquear el mic
    frente a Node. Ver docs/alsa_mic_compartido.md.
    """
    return resolver_dispositivo_entrada_config()


# =============================================================================
# COMANDOS CLI
# Cada _cmd_* corresponde a un flag de argparse y no contiene lógica de negocio:
# solo resuelve el dispositivo y delega en el módulo especializado.
# =============================================================================

def _cmd_listar_dispositivos() -> None:
    """Imprime dispositivos de entrada; use índice o MIC_NOMBRE_CONTIENE en config.py."""
    listar_dispositivos_entrada(imprimir=True)


def _cmd_comprobar_dispositivo_entrada() -> None:
    """
    Resuelve el micrófono como en una grabación real y lee un instante de audio.

    Falla con código distinto de cero si no hay dispositivo, hay ambigüedad por
    nombre o la captura no devuelve datos válidos.
    """
    dev = _dispositivo_entrada_resuelto()
    print(f"Dispositivo resuelto: {describir_dispositivo_entrada(dev)}")
    tasa, rms = comprobar_entrada_entrega_muestras(
        dev,
        tasa_muestreo_hz=config.TASA_MUESTREO_HZ,
        canales=config.CANALES,
    )
    print(f"Comprobación OK: captura a {tasa} Hz, RMS aproximado={rms:.6f} (silencio ≈ 0).")


def _cmd_stream_chunks(
    duracion: float,
    *,
    intervalo_stats: float | None,
    marcos_bloque: int | None,
) -> None:
    """Escucha continua en bloques con métricas; no escribe WAV."""
    dev = _dispositivo_entrada_resuelto()
    print(f"Dispositivo: {describir_dispositivo_entrada(dev)}")
    ejecutar_escucha_continua(
        duracion,
        dispositivo=dev,
        tasa_muestreo_solicitada_hz=config.TASA_MUESTREO_HZ,
        canales=config.CANALES,
        marcos_por_bloque=(
            config.CAPTURA_CONTINUA_BLOQUE_MUESTRAS if marcos_bloque is None else marcos_bloque
        ),
        latencia=config.CAPTURA_CONTINUA_LATENCIA,
        estadisticas_cada_seg=(
            config.CAPTURA_CONTINUA_INFORME_STATS_S if intervalo_stats is None else intervalo_stats
        ),
    )


def _cmd_list_intents() -> None:
    """Lista intenciones definidas en el catálogo JSON."""
    print(f" ruta: {config.CATALOGO_INTENCIONES_RUTA}")
    cat = cargar_catalogo(config.CATALOGO_INTENCIONES_RUTA)
    print(f"Catálogo: v{cat.get('version')} ({config.CATALOGO_INTENCIONES_RUTA})")
    for it in cat.get("intenciones") or []:
        print(f"  - {it.get('id', '?')}: {it.get('titulo', '')}")


def _cmd_test_oracion(oracion: str) -> None:
    """Empareja una oración contra el catálogo y ejecuta el manejador del id detectado."""
    cat = cargar_catalogo(config.CATALOGO_INTENCIONES_RUTA)
    hit = emparejar_intencion(cat, oracion)
    if hit is None:
        print("Sin intención coincidente para esta oración.")
        return
    print(
        f"Intención: {hit.intencion_id} ({hit.intencion_titulo}) "
        f"disparador={hit.disparador!r} | tras_wake={hit.texto_tras_wake!r}"
    )
    ejecutar_intencion(hit.intencion_id, bloqueante=True)


def _cmd_wake_turn() -> None:
    """
    Punto de entrada CLI para --wake-turn.

    No contiene lógica de negocio: resuelve el micrófono y delega en
    pipeline.ejecutar_turno_wake_grabar_stt_intent. Todos los tiempos,
    modelos y rutas vienen de config.py.
    """
    dev = _dispositivo_entrada_resuelto()
    print(f"Dispositivo: {describir_dispositivo_entrada(dev)}")
    ejecutar_turno_wake_grabar_stt_intent(
        dispositivo_entrada=dev,
        # --- Wake word (openWakeWord) ---
        timeout_espera_wake_seg=config.WAKE_TURN_TIMEOUT_SEG,
        modelos_wake=list(config.OPENWAKEWORD_MODELOS),
        frase_activacion=config.FRASE_ACTIVACION,
        umbral_wake=config.OPENWAKEWORD_UMBRAL,
        rebote_wake_seg=config.OPENWAKEWORD_REBOTE_SEG,
        inferencia_wake=config.OPENWAKEWORD_INFERENCIA,
        vad_umbral_wake=config.OPENWAKEWORD_VAD_UMBRAL,
        blocksize_wake=config.OPENWAKEWORD_BLOQUE_STREAM_MUESTRAS,
        ruta_audio_confirmacion_wake=config.OPENWAKEWORD_AUDIO_CONFIRMACION,
        # --- Captura de la orden tras el wake ---
        silencio_post_wake_seg=config.POST_WAKE_SILENCIO_SEG,
        duracion_grabacion_orden_seg=config.POST_WAKE_GRABAR_ORDEN_SEG,
        tasa_muestreo_hz=config.TASA_MUESTREO_HZ,
        canales=config.CANALES,
        tasa_salida_pipeline_hz=config.TASA_SALIDA_PIPELINE_HZ,
        carpeta_grabaciones=config.CARPETA_GRABACIONES,
        guardar_wav_debug=config.WAKE_TURN_GUARDAR_WAV_DEBUG,
        # --- STT (faster-whisper) ---
        whisper_modelo=config.WHISPER_MODELO,
        whisper_dispositivo=config.WHISPER_DISPOSITIVO,
        whisper_tipo_computo=config.WHISPER_TIPO_COMPUTO,
        whisper_idioma=config.WHISPER_IDIOMA,
        # --- Catálogo de intenciones ---
        catalogo_intenciones_ruta=config.CATALOGO_INTENCIONES_RUTA,
    )


def _cmd_wake_listen(duracion: float) -> None:
    """Escucha continua con openWakeWord; modelos y umbral vienen de config."""
    dev = _dispositivo_entrada_resuelto()
    print(f"Dispositivo: {describir_dispositivo_entrada(dev)}")
    ejecutar_escucha_openwakeword(
        duracion,
        dispositivo=dev,
        tasa_muestreo_solicitada_hz=config.TASA_MUESTREO_HZ,
        canales=config.CANALES,
        modelos=config.OPENWAKEWORD_MODELOS,
        frase_objetivo_producto=config.FRASE_ACTIVACION,
        umbral=config.OPENWAKEWORD_UMBRAL,
        rebote_segundos=config.OPENWAKEWORD_REBOTE_SEG,
        inferencia=config.OPENWAKEWORD_INFERENCIA,
        vad_umbral=config.OPENWAKEWORD_VAD_UMBRAL,
        blocksize=config.OPENWAKEWORD_BLOQUE_STREAM_MUESTRAS,
        ruta_audio_wake=config.OPENWAKEWORD_AUDIO_CONFIRMACION,
    )


def _cmd_prueba_grabacion(duracion: float, *, salida_pipeline: bool) -> Path:
    """
    Graba ``duracion`` segundos y guarda un WAV en config.CARPETA_GRABACIONES.

    Con salida_pipeline=True (por defecto): mono PCM 16-bit a 16 kHz (listo para STT).
    Con salida_pipeline=False: WAV a la tasa nativa del micrófono (útil para depurar hw).

    Returns:
        Ruta del archivo generado.
    """
    carpeta = Path(config.CARPETA_GRABACIONES)
    carpeta.mkdir(parents=True, exist_ok=True)
    sufijo = "pipeline" if salida_pipeline else "raw"
    nombre = datetime.now().strftime(f"prueba_{sufijo}_%Y%m%d_%H%M%S.wav")
    salida = carpeta / nombre

    dev = _dispositivo_entrada_resuelto()
    print(
        f"Grabando {duracion} s (solicitado {config.TASA_MUESTREO_HZ} Hz), "
        f"dispositivo={describir_dispositivo_entrada(dev)}..."
    )
    muestras, tasa_efectiva = grabar_muestras(
        duracion,
        dispositivo=dev,
        tasa_muestreo_hz=config.TASA_MUESTREO_HZ,
        canales=config.CANALES,
    )
    if tasa_efectiva != config.TASA_MUESTREO_HZ:
        print(
            f"Tasa efectiva de la captura: {tasa_efectiva} Hz "
            f"(solicitado {config.TASA_MUESTREO_HZ} Hz)."
        )

    if salida_pipeline:
        audio_out, tasa_wav = preparar_muestras_para_stt(
            muestras, tasa_efectiva, config.TASA_SALIDA_PIPELINE_HZ
        )
        if tasa_efectiva != tasa_wav:
            print(f"Salida pipeline: remuestreado a {tasa_wav} Hz mono.")
        else:
            print(f"Salida pipeline: {tasa_wav} Hz mono (sin remuestreo).")
    else:
        audio_out, tasa_wav = muestras, tasa_efectiva
        print(f"Salida raw: WAV a {tasa_wav} Hz (tasa de captura, sin normalizar).")

    ruta = guardar_wav_mono(salida, audio_out, tasa_wav)
    print(f"Guardado: {ruta}")
    print("Reproducción sugerida: aplay " + str(ruta))
    return ruta


# =============================================================================
# ARGPARSE + DISPATCHER
# =============================================================================

def main() -> None:
    """Parsea argumentos CLI y ejecuta la suborden solicitada."""
    parser = argparse.ArgumentParser(description="Asistente de voz — pruebas de audio")
    parser.add_argument(
        "--list-devices", action="store_true",
        help="Lista micrófonos (índices PortAudio); configure MIC_NOMBRE_CONTIENE o DISPOSITIVO_ENTRADA",
    )
    parser.add_argument(
        "--check-input-device", action="store_true",
        help="Comprueba que el micrófono resuelto desde config existe y entrega muestras válidas",
    )
    parser.add_argument(
        "--test-record", type=float, metavar="SEG",
        help=(
            "Graba SEG segundos y guarda WAV (por defecto: mono 16 kHz PCM16 listo para STT; "
            "carpeta en config.CARPETA_GRABACIONES)"
        ),
    )
    parser.add_argument(
        "--raw-device-rate", action="store_true",
        help="Solo con --test-record: no remuestrear; el WAV usa la tasa real del micrófono",
    )
    parser.add_argument(
        "--stream-chunks", type=float, nargs="?", const=30.0, default=None, metavar="SEG",
        help="Escucha continua por bloques (no guarda audio). SEG=segundos; sin valor → 30; 0 → hasta Ctrl+C",
    )
    parser.add_argument(
        "--stream-stats-interval", type=float, default=None, metavar="SEG",
        help="Solo con --stream-chunks: segundos entre informes de métricas (predeterminado: config)",
    )
    parser.add_argument(
        "--stream-blocksize", type=int, default=None, metavar="MARCOS",
        help="Solo con --stream-chunks: marcos por callback (predeterminado: config)",
    )
    parser.add_argument(
        "--wake-listen", type=float, nargs="?", const=60.0, default=None, metavar="SEG",
        help=(
            "openWakeWord en vivo (no guarda audio). SEG=segundos; sin valor → 60; "
            "0 → hasta Ctrl+C. Parámetros: config.OPENWAKEWORD_*"
        ),
    )
    parser.add_argument(
        "--list-intents", action="store_true",
        help="Lista intenciones del catálogo (data/catalogo_intenciones.json)",
    )
    parser.add_argument(
        "--test-oracion", type=str, metavar="TEXTO",
        help='Prueba emparejo + acción (ej.: --test-oracion "Hex vox device hola")',
    )
    parser.add_argument(
        "--wake-turn", action="store_true",
        help="Un turno: wake → grabar orden → Whisper local → intención (ver config WAKE_TURN_*, WHISPER_*)",
    )
    args = parser.parse_args()

    if args.list_devices:
        _cmd_listar_dispositivos()
    elif args.check_input_device:
        _cmd_comprobar_dispositivo_entrada()
    elif args.stream_chunks is not None:
        _cmd_stream_chunks(
            args.stream_chunks,
            intervalo_stats=args.stream_stats_interval,
            marcos_bloque=args.stream_blocksize,
        )
    elif args.wake_listen is not None:
        _cmd_wake_listen(args.wake_listen)
    elif args.wake_turn:
        _cmd_wake_turn()
    elif args.list_intents:
        _cmd_list_intents()
    elif args.test_oracion is not None:
        _cmd_test_oracion(args.test_oracion)
    elif args.test_record is not None:
        _cmd_prueba_grabacion(args.test_record, salida_pipeline=not args.raw_device_rate)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
