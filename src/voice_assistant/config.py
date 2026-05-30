"""
Configuración global del asistente de voz.

Todos los parámetros ajustables por el operador viven aquí: micrófono, audio,
wake word, STT, intenciones y Redis. La lógica de cada módulo lee de este
archivo; para cambiar el comportamiento nunca hay que tocar el código de flujo.

Flujo de referencia:
    main.py → pipeline.py → wake.py → stt.py → intents.py → redis_reunion.py
                                └── audio.py (primitivas de captura en todos los pasos)
"""

# =============================================================================
# COLORES DE CONSOLA (ANSI)
# Usados en pipeline.py para resaltar la fase de grabación de la orden.
# =============================================================================
COLOR = {
    "rojo": "\033[0;31m",
    "verde": "\033[0;32m",
    "azul": "\033[0;34m",
    "reset": "\033[0m",
}

# =============================================================================
# MICRÓFONO
# =============================================================================

# Si no es None ni cadena vacía, tiene prioridad: primer micrófono cuyo nombre
# PortAudio contiene esta subcadena (sin distinguir mayúsculas).
#
# En Raspberry con ``pcm.compartido`` (dsnoop en /etc/asound.conf) use
# ``"compartido"`` para capturar en paralelo con Node.js sin bloquear hw:0,0.
# Ver docs/alsa_mic_compartido.md.
MIC_NOMBRE_CONTIENE: str | None = "compartido"

# Índice PortAudio; solo si ``MIC_NOMBRE_CONTIENE`` es None o vacío.
# None = predeterminado del sistema (evitar si usa dsnoop: suele ser PipeWire).
DISPOSITIVO_ENTRADA: int | None = None

# Nombre del PCM ALSA definido en asound.conf (documentación / Node).
# PortAudio usa ``MIC_NOMBRE_CONTIENE`` para elegir el dispositivo en Python.
MIC_ALSA_PCM: str = "compartido"

# =============================================================================
# AUDIO — TASAS Y FORMATO
# =============================================================================

# Frecuencia de muestreo deseada en Hz. Si el hardware no la admite,
# ``audio.py`` reintenta con la tasa nativa del dispositivo (p. ej. 48 000).
TASA_MUESTREO_HZ: int = 16_000

# Tasa de salida del pipeline hacia STT (Whisper espera 16 kHz mono).
# Si la cambias, ajusta también el modelo de STT.
TASA_SALIDA_PIPELINE_HZ: int = 16_000

# Grabación monoaural; la mayoría de micrófonos USB expone 1 canal.
CANALES: int = 1

# =============================================================================
# AUDIO — ESCUCHA CONTINUA (comando --stream-chunks, debug/métricas)
# =============================================================================

# Muestras por callback PortAudio. Ej.: 512 a 16 kHz ≈ 32 ms por bloque.
CAPTURA_CONTINUA_BLOQUE_MUESTRAS: int = 512
# Latencia del stream: "low", "high" o segundos (ver sounddevice.InputStream).
CAPTURA_CONTINUA_LATENCIA: str | float = "low"
# Periodo entre líneas de métricas (s) al usar ``main.py --stream-chunks``.
CAPTURA_CONTINUA_INFORME_STATS_S: float = 1.0

# =============================================================================
# RUTAS
# =============================================================================

# Carpeta donde se guardan grabaciones de prueba y WAVs de depuración.
CARPETA_GRABACIONES: str = "recordings"

# Catálogo de intenciones post-wakeup (JSON relativo a la raíz del repo).
CATALOGO_INTENCIONES_RUTA: str = "data/catalogo_intenciones.json"

# =============================================================================
# WAKE WORD — openWakeWord
# =============================================================================

# Frase de producto (UX/documentación). OWW no la "lee": usa los modelos ONNX
# de OPENWAKEWORD_MODELOS. Hace falta un modelo custom para esta frase concreta.
FRASE_ACTIVACION: str = "hey vox device"

# Rutas a modelos .onnx/.tflite o nombres incluidos (p. ej. "hey_mycroft").
OPENWAKEWORD_MODELOS: list[str] = [
    "/home/pi/vox_device_main_thread/voice-assistant/models/wakewords/hey_box_device.onnx"
]
OPENWAKEWORD_UMBRAL: float = 0.5
OPENWAKEWORD_REBOTE_SEG: float = 1.2
OPENWAKEWORD_INFERENCIA: str = "onnx"       # "onnx" o "tflite"
OPENWAKEWORD_VAD_UMBRAL: float = 0.0        # >0 activa Silero VAD dentro de OWW
# blocksize del InputStream; None = predeterminado PortAudio.
OPENWAKEWORD_BLOQUE_STREAM_MUESTRAS: int | None = None
# Audio de confirmación al detectar wakeword (WAV PCM16 recomendado).
OPENWAKEWORD_AUDIO_CONFIRMACION: str = "audio_messages/wake_sound.wav"

# =============================================================================
# PIPELINE — turno completo wake → grabar → Whisper → intención
# =============================================================================

WAKE_TURN_TIMEOUT_SEG: float = 120.0
POST_WAKE_SILENCIO_SEG: float = 0
POST_WAKE_GRABAR_ORDEN_SEG: float = 5.0
# Si True, guarda un WAV de la orden en CARPETA_GRABACIONES para depuración.
WAKE_TURN_GUARDAR_WAV_DEBUG: bool = False

# =============================================================================
# STT — faster-whisper
# =============================================================================

WHISPER_MODELO: str = "tiny"
WHISPER_DISPOSITIVO: str = "cpu"
WHISPER_TIPO_COMPUTO: str = "int8"
WHISPER_IDIOMA: str = "es"

# =============================================================================
# INTENCIONES — manejadores
# =============================================================================

# Segundos de escucha adicional en ``nueva_reunion`` para capturar el nombre.
NUEVA_REUNION_ESCUCHA_SEG: float = 5.0

# =============================================================================
# REDIS — comunicación con el flujo Node.js en la misma Raspberry
# =============================================================================

REDIS_HABILITADO: bool = True
REDIS_HOST: str = "127.0.0.1"
REDIS_PORT: int = 6379
REDIS_DB: int = 0
REDIS_PASSWORD: str | None = None
REDIS_SOCKET_TIMEOUT_SEG: float = 2.0

# Pub/Sub bidireccional (ver docs/redis_reunion_node.md).
REDIS_CANAL_COMANDOS: str = "vox:reunion:comandos"    # Python → Node
REDIS_CANAL_RESPUESTAS: str = "vox:reunion:respuestas"  # Node → Python

# Clave de respaldo con el último comando publicado (GET alternativo para Node).
REDIS_CLAVE_ULTIMA_SOLICITUD: str = "vox:reunion:ultima_solicitud"
# Clave por solicitud: vox:reunion:respuesta:<solicitud_id>
REDIS_CLAVE_RESPUESTA_PREFIJO: str = "vox:reunion:respuesta:"

REDIS_SOLICITUD_TTL_SEG: int = 3600
REDIS_RESPUESTA_TTL_SEG: int = 120
# Tiempo máximo esperando respuesta de Node tras ``iniciar_reunion``.
REDIS_RESPUESTA_TIMEOUT_SEG: float = 5.0
