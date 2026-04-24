"""
Punto de entrada del asistente de voz: pruebas de micrófono y grabación a WAV.

Instalar el paquete en modo editable (recomendado; así ``python main.py`` encuentra el módulo):

    source venv/bin/activate
    pip install -r requirements.txt
    pip install -e .
    python main.py --list-devices
    python main.py --test-record 5

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
from voice_assistant import config


def _cmd_listar_dispositivos() -> None:
    """Imprime dispositivos de entrada; elija el índice y póngalo en `config.DISPOSITIVO_ENTRADA`."""
    listar_dispositivos_entrada(imprimir=True)


def _cmd_prueba_grabacion(duracion: float) -> Path:
    """
    Graba `duracion` segundos y guarda un WAV en `config.CARPETA_GRABACIONES`.

    Returns:
        Ruta del archivo generado.
    """
    carpeta = Path(config.CARPETA_GRABACIONES)
    carpeta.mkdir(parents=True, exist_ok=True)
    nombre = datetime.now().strftime("prueba_%Y%m%d_%H%M%S.wav")
    salida = carpeta / nombre

    print(
        f"Grabando {duracion} s (solicitado {config.TASA_MUESTREO_HZ} Hz), "
        f"dispositivo={config.DISPOSITIVO_ENTRADA!r}..."
    )
    muestras, tasa_efectiva = grabar_muestras(
        duracion,
        dispositivo=config.DISPOSITIVO_ENTRADA,
        tasa_muestreo_hz=config.TASA_MUESTREO_HZ,
        canales=config.CANALES,
    )
    if tasa_efectiva != config.TASA_MUESTREO_HZ:
        print(f"Tasa efectiva de la grabación: {tasa_efectiva} Hz (el WAV usa esta tasa).")
    ruta = guardar_wav_mono(salida, muestras, tasa_efectiva)
    print(f"Guardado: {ruta}")
    print("Reproducción sugerida: aplay " + str(ruta))
    return ruta


def main() -> None:
    """Parsea argumentos CLI y ejecuta la suborden solicitada."""
    parser = argparse.ArgumentParser(description="Asistente de voz — pruebas de audio")
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="Lista micrófonos (índices PortAudio) para configurar DISPOSITIVO_ENTRADA",
    )
    parser.add_argument(
        "--test-record",
        type=float,
        metavar="SEG",
        help="Graba SEG segundos y guarda WAV en la carpeta recordings/",
    )
    args = parser.parse_args()

    if args.list_devices:
        _cmd_listar_dispositivos()
        return

    if args.test_record is not None:
        _cmd_prueba_grabacion(args.test_record)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
