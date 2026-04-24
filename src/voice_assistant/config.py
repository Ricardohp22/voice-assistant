"""
Parámetros globales de audio fáciles de ajustar sin tocar la lógica de captura.

Micrófono (iteración 3): tras ``python main.py --list-devices``, puede fijar
``MIC_NOMBRE_CONTIENE`` (p. ej. ``"USB"``) para sobrevivir a cambios de orden
de los índices, o dejar ``None`` y usar solo ``DISPOSITIVO_ENTRADA``.
"""

# Si no es None ni cadena vacía, tiene prioridad: primer micrófono cuyo nombre PortAudio
# contiene esta subcadena (sin distinguir mayúsculas). Valor por defecto acorde a mic USB.
MIC_NOMBRE_CONTIENE: str | None = "USB"

# Índice del dispositivo de entrada (sounddevice / PortAudio). Se usa si
# ``MIC_NOMBRE_CONTIENE`` es None o vacío. None = predeterminado del sistema.
#
# Referencia ALSA: en ``arecord -l`` suele aparecer como card 0, device 0 → ``hw:0,0``.
# PortAudio usa su propia numeración; con un solo mic USB suele coincidir el índice ``0``,
# pero no está garantizado: use ``--list-devices`` si cambia el hardware.
DISPOSITIVO_ENTRADA: int | None = 0

# Frecuencia de muestreo deseada en Hz (16 kHz es habitual en voz).
# Si abres el micrófono USB como dispositivo ``hw`` (p. ej. índice 0) y el hardware
# no admite esta tasa, ``grabar_muestras`` reintentará con la tasa predeterminada
# del dispositivo (suele ser 44100 o 48000). ``default``/PipeWire suele aceptar 16 kHz.
TASA_MUESTREO_HZ: int = 16_000

# Tasa del WAV **listo para STT** tras la normalización (iteración 2). Whisper suele
# usar 16 kHz mono; si cambias esto, ajusta también el modelo / herramienta de STT.
TASA_SALIDA_PIPELINE_HZ: int = 16_000

# Grabación monoaural; la mayoría de micrófonos USB expone 1 canal.
CANALES: int = 1

# --- Captura continua (iteración 4): stream por chunks sin guardar audio ---
# Muestras por callback PortAudio. Ej.: 512 a 16 kHz ≈ 32 ms por bloque.
CAPTURA_CONTINUA_BLOQUE_MUESTRAS: int = 512
# Latencia del stream: "low", "high" o segundos (ver sounddevice.InputStream).
CAPTURA_CONTINUA_LATENCIA: str | float = "low"
# Periodo entre líneas de métricas (s) al usar ``main.py --stream-chunks``.
CAPTURA_CONTINUA_INFORME_STATS_S: float = 1.0

# Carpeta donde se guardan las pruebas de grabación (relativa al cwd al ejecutar).
CARPETA_GRABACIONES: str = "recordings"

# Frase de activación prevista (wake word / Porcupine en una iteración futura).
FRASE_ACTIVACION: str = "hi box translate"
