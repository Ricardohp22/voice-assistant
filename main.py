"""
Punto de entrada del asistente de voz: pruebas de micrófono y grabación a WAV.

Instalar el paquete en modo editable (recomendado; así ``python main.py`` encuentra el módulo):

    source venv/bin/activate
    pip install -r requirements.txt
    pip install -e .
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

Comandos útiles en Linux (ALSA) para ver hardware sin Python:

    arecord -l
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from voice_assistant.audio.capture import (
    grabar_muestras,
    guardar_wav_mono,
    listar_dispositivos_entrada,
)
from voice_assistant.audio.dispositivo import (
    comprobar_entrada_entrega_muestras,
    describir_dispositivo_entrada,
    resolver_dispositivo_entrada,
)
from voice_assistant.audio.captura_continua import ejecutar_escucha_continua
from voice_assistant.audio.formato_pipeline import preparar_muestras_para_stt
from voice_assistant.intents import cargar_catalogo, emparejar_intencion, ejecutar_intencion
from voice_assistant.pipeline import ejecutar_turno_wake_grabar_stt_intent
from voice_assistant.wake import ejecutar_escucha_openwakeword
from voice_assistant import config


def _dispositivo_entrada_resuelto() -> int | None:
    """
    Índice PortAudio según ``config`` (nombre tiene prioridad sobre índice).

    Usado por ``--wake-turn`` y el resto de comandos de audio para abrir siempre
    el mismo micrófono que el operador configuró en ``config.py``.
    """
    return resolver_dispositivo_entrada(
        config.MIC_NOMBRE_CONTIENE,
        config.DISPOSITIVO_ENTRADA,
    )


def _cmd_listar_dispositivos() -> None:
    """Imprime dispositivos de entrada; use índice o ``MIC_NOMBRE_CONTIENE`` en ``config.py``."""
    listar_dispositivos_entrada(imprimir=True)


def _cmd_comprobar_dispositivo_entrada() -> None:
    """
    Resuelve el micrófono como en una grabación real y lee un instante de audio.

    Falla con código distinto de cero si no hay dispositivo, hay ambigüedad por nombre
    o la captura no devuelve datos válidos.
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
    """Escucha continua en bloques con métricas (iteración 4); no escribe WAV."""
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
    cat = cargar_catalogo(config.CATALOGO_INTENCIONES_RUTA)
    print(f"Catálogo: v{cat.get('version')} ({config.CATALOGO_INTENCIONES_RUTA})")
    for it in cat.get("intenciones") or []:
        iid = it.get("id", "?")
        tit = it.get("titulo", "")
        print(f"  - {iid}: {tit}")


def _cmd_test_oracion(oracion: str) -> None:
    """Empareja una oración contra el catálogo y ejecuta el manejador del ``id`` detectado."""
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
    Punto de entrada CLI para ``--wake-turn``.

    No contiene lógica de negocio: resuelve el micrófono y delega en
    ``ejecutar_turno_wake_grabar_stt_intent`` (pipeline). Todos los tiempos,
    modelos y rutas vienen de ``config.py`` para que un colaborador ajuste
    el comportamiento sin tocar este archivo.
    """
    # Mismo criterio de micrófono que --test-record o --wake-listen.
    dev = _dispositivo_entrada_resuelto()
    print(f"Dispositivo: {describir_dispositivo_entrada(dev)}")

    # Orquestación del turno (wake → grabar → STT → intención → acción).
    ejecutar_turno_wake_grabar_stt_intent(
        dispositivo_entrada=dev,
        # --- Espera de wake (openWakeWord) ---
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
        # --- STT local (faster-whisper) ---
        whisper_modelo=config.WHISPER_MODELO,
        whisper_dispositivo=config.WHISPER_DISPOSITIVO,
        whisper_tipo_computo=config.WHISPER_TIPO_COMPUTO,
        whisper_idioma=config.WHISPER_IDIOMA,
        # --- Catálogo de intenciones post-wake ---
        catalogo_intenciones_ruta=config.CATALOGO_INTENCIONES_RUTA,
    )


def _cmd_wake_listen(duracion: float) -> None:
    """Escucha con openWakeWord (iteración 5); modelos y umbral vienen de ``config``."""
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
    Graba `duracion` segundos y guarda un WAV en `config.CARPETA_GRABACIONES`.

    Por defecto (salida_pipeline=True) el archivo queda **listo para STT**:
    mono, PCM 16-bit, ``config.TASA_SALIDA_PIPELINE_HZ`` (p. ej. 16 kHz), aunque el
    micrófono haya grabado a otra tasa (remuestreo lineal).

    Con ``salida_pipeline=False`` se conserva la tasa nativa de la captura (útil
    para depurar hardware sin remuestreo).

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
            f"(solicitado al abrir el dispositivo era {config.TASA_MUESTREO_HZ} Hz)."
        )

    if salida_pipeline:
        audio_out, tasa_wav = preparar_muestras_para_stt(
            muestras,
            tasa_efectiva,
            config.TASA_SALIDA_PIPELINE_HZ,
        )
        if tasa_efectiva != tasa_wav:
            print(
                f"Salida pipeline: remuestreado a {tasa_wav} Hz mono "
                f"(contrato STT; ver config.TASA_SALIDA_PIPELINE_HZ)."
            )
        else:
            print(
                f"Salida pipeline: {tasa_wav} Hz mono (sin remuestreo; ya coincidía con la captura)."
            )
    else:
        audio_out, tasa_wav = muestras, tasa_efectiva
        print(f"Salida raw: WAV a {tasa_wav} Hz (tasa de la captura, sin normalizar a pipeline).")

    ruta = guardar_wav_mono(salida, audio_out, tasa_wav)
    print(f"Guardado: {ruta}")
    print("Reproducción sugerida: aplay " + str(ruta))
    return ruta


def main() -> None:
    """Parsea argumentos CLI y ejecuta la suborden solicitada."""
    parser = argparse.ArgumentParser(description="Asistente de voz — pruebas de audio")
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Lista micrófonos (índices PortAudio); configure MIC_NOMBRE_CONTIENE o DISPOSITIVO_ENTRADA",
    )
    parser.add_argument(
        "--check-input-device",
        action="store_true",
        help="Comprueba que el micrófono resuelto desde config existe y entrega muestras válidas",
    )
    parser.add_argument(
        "--test-record",
        type=float,
        metavar="SEG",
        help=(
            "Graba SEG segundos y guarda WAV (por defecto: mono 16 kHz PCM16 listo para STT; "
            "carpeta en config.CARPETA_GRABACIONES)"
        ),
    )
    parser.add_argument(
        "--raw-device-rate",
        action="store_true",
        help=(
            "Solo con --test-record: no remuestrear; el WAV usa la tasa real del micrófono "
            "(p. ej. 48000 Hz)"
        ),
    )
    parser.add_argument(
        "--stream-chunks",
        type=float,
        nargs="?",
        const=30.0,
        default=None,
        metavar="SEG",
        help=(
            "Iteración 4: escucha continua por bloques (no guarda audio). "
            "SEG=segundos; sin valor → 30; 0 → hasta Ctrl+C"
        ),
    )
    parser.add_argument(
        "--stream-stats-interval",
        type=float,
        default=None,
        metavar="SEG",
        help="Solo con --stream-chunks: segundos entre informes de métricas (predeterminado: config)",
    )
    parser.add_argument(
        "--stream-blocksize",
        type=int,
        default=None,
        metavar="MARCOS",
        help="Solo con --stream-chunks: marcos por callback (predeterminado: config)",
    )
    parser.add_argument(
        "--wake-listen",
        type=float,
        nargs="?",
        const=60.0,
        default=None,
        metavar="SEG",
        help=(
            "Iteración 5: openWakeWord en vivo (no guarda audio). SEG=segundos; sin valor → 60; "
            "0 → hasta Ctrl+C. Parámetros: config.OPENWAKEWORD_* y FRASE_ACTIVACION"
        ),
    )
    parser.add_argument(
        "--list-intents",
        action="store_true",
        help="Lista intenciones del catálogo (data/catalogo_intenciones.json; ruta en config)",
    )
    parser.add_argument(
        "--test-oracion",
        type=str,
        metavar="TEXTO",
        help='Prueba emparejo local + acción (ej.: --test-oracion "Hex vox device hola")',
    )
    parser.add_argument(
        "--wake-turn",
        action="store_true",
        help="Un turno: wake → grabar orden → Whisper local → intención (ver config WAKE_TURN_*, WHISPER_*)",
    )
    args = parser.parse_args()

    if args.list_devices:
        _cmd_listar_dispositivos()
        return

    if args.check_input_device:
        _cmd_comprobar_dispositivo_entrada()
        return

    if args.stream_chunks is not None:
        _cmd_stream_chunks(
            args.stream_chunks,
            intervalo_stats=args.stream_stats_interval,
            marcos_bloque=args.stream_blocksize,
        )
        return

    if args.wake_listen is not None:
        _cmd_wake_listen(args.wake_listen)
        return

    if args.wake_turn:
        _cmd_wake_turn()
        return

    if args.list_intents:
        _cmd_list_intents()
        return

    if args.test_oracion is not None:
        _cmd_test_oracion(args.test_oracion)
        return

    if args.test_record is not None:
        _cmd_prueba_grabacion(args.test_record, salida_pipeline=not args.raw_device_rate)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
