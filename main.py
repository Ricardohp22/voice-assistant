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
from voice_assistant.audio.formato_pipeline import preparar_muestras_para_stt
from voice_assistant import config


def _dispositivo_entrada_resuelto() -> int | None:
    """Índice PortAudio según ``config`` (nombre tiene prioridad sobre índice)."""
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
    args = parser.parse_args()

    if args.list_devices:
        _cmd_listar_dispositivos()
        return

    if args.check_input_device:
        _cmd_comprobar_dispositivo_entrada()
        return

    if args.test_record is not None:
        _cmd_prueba_grabacion(args.test_record, salida_pipeline=not args.raw_device_rate)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
