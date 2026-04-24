"""
Parámetros globales de audio fáciles de ajustar sin tocar la lógica de captura.

Tras listar dispositivos (main.py --list-devices o listar_dispositivos_entrada),
asigna aquí el índice del micrófono deseado, o deja None para el predeterminado
del sistema.
"""

# Índice del dispositivo de entrada (sounddevice / PortAudio). None = predeterminado.
DISPOSITIVO_ENTRADA: int | None = 0

# Frecuencia de muestreo deseada en Hz (16 kHz es habitual en voz).
# Si abres el micrófono USB como dispositivo ``hw`` (p. ej. índice 0) y el hardware
# no admite esta tasa, ``grabar_muestras`` reintentará con la tasa predeterminada
# del dispositivo (suele ser 44100 o 48000). ``default``/PipeWire suele aceptar 16 kHz.
TASA_MUESTREO_HZ: int = 16_000

# Grabación monoaural; la mayoría de micrófonos USB expone 1 canal.
CANALES: int = 1

# Carpeta donde se guardan las pruebas de grabación (relativa al cwd al ejecutar).
CARPETA_GRABACIONES: str = "recordings"
