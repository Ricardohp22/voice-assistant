"""
Parámetros globales de audio fáciles de ajustar sin tocar la lógica de captura.

Micrófono (iteración 3): tras ``python main.py --list-devices``, puede fijar
``MIC_NOMBRE_CONTIENE`` (p. ej. ``"USB"``) para sobrevivir a cambios de orden
de los índices, o dejar ``None`` y usar solo ``DISPOSITIVO_ENTRADA``.
"""

# Si no es None ni cadena vacía, tiene prioridad: primer micrófono cuyo nombre PortAudio
# contiene esta subcadena (sin distinguir mayúsculas).
#
# En Raspberry con ``pcm.compartido`` (dsnoop en /etc/asound.conf) use ``"compartido"``
# para capturar en paralelo con Node.js sin bloquear ``hw:0,0``. Ver docs/alsa_mic_compartido.md.
# No use ``"USB"`` / índice del hw directo si otro proceso también necesita el mic.
MIC_NOMBRE_CONTIENE: str | None = "compartido"

# Índice PortAudio; solo si ``MIC_NOMBRE_CONTIENE`` es None o vacío.
# None = predeterminado del sistema (evitar si usa dsnoop: suele ser PipeWire ``default``).
DISPOSITIVO_ENTRADA: int | None = None

# Nombre del PCM ALSA definido en asound.conf (documentación / Node); PortAudio usa
# ``MIC_NOMBRE_CONTIENE`` para elegir el dispositivo en Python.
MIC_ALSA_PCM: str = "compartido"

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

# Frase de activación **de producto** (UX, TTS, documentación). openWakeWord no
# la “lee”: solo ejecuta los modelos listados en OPENWAKEWORD_MODELOS. Para esta
# frase concreta hace falta un modelo custom entrenado; mientras tanto use un
# modelo incluido (p. ej. hey_mycroft) para probar el pipeline en la Raspberry.
FRASE_ACTIVACION: str = "hey vox device"

# --- openWakeWord (iteración 5) ---
# Nombres de modelos incluidos (p. ej. "hey_mycroft", "alexa") y/o rutas a .onnx/.tflite.
OPENWAKEWORD_MODELOS: list[str] = [
    "/home/pi/vox_device_main_thread/voice-assistant/models/wakewords/hey_box_device.onnx"
]
OPENWAKEWORD_UMBRAL: float = 0.5
OPENWAKEWORD_REBOTE_SEG: float = 1.2
OPENWAKEWORD_INFERENCIA: str = "onnx"  # "onnx" o "tflite"
OPENWAKEWORD_VAD_UMBRAL: float = 0.0  # >0 activa Silero VAD dentro de OWW
# blocksize del InputStream; None = predeterminado PortAudio (suele bastar).
OPENWAKEWORD_BLOQUE_STREAM_MUESTRAS: int | None = None
# Audio de confirmación al detectar wakeword (WAV PCM16 recomendado).
OPENWAKEWORD_AUDIO_CONFIRMACION: str = "audio_messages/wake_sound.wav"

# Catálogo de intenciones post-wakeup (JSON relativo a la raíz del repositorio).
CATALOGO_INTENCIONES_RUTA: str = "data/catalogo_intenciones.json"

# --- Turno completo: wake → grabar → Whisper → intención (pipeline) ---
WAKE_TURN_TIMEOUT_SEG: float = 120.0
POST_WAKE_SILENCIO_SEG: float = 0
POST_WAKE_GRABAR_ORDEN_SEG: float = 5.0
# Si True, guarda un WAV de la orden en CARPETA_GRABACIONES tras el wake.
WAKE_TURN_GUARDAR_WAV_DEBUG: bool = False

WHISPER_MODELO: str = "tiny"
WHISPER_DISPOSITIVO: str = "cpu"
WHISPER_TIPO_COMPUTO: str = "int8"
WHISPER_IDIOMA: str = "es"

# Tras ``manejar_nueva_reunion``: segundos de escucha adicional para capturar datos (p. ej. nombre).
NUEVA_REUNION_ESCUCHA_SEG: float = 5.0

# --- Redis (comunicación con flujo Node.js en la misma Raspberry) ---
REDIS_HABILITADO: bool = True
REDIS_HOST: str = "127.0.0.1"
REDIS_PORT: int = 6379
REDIS_DB: int = 0
REDIS_PASSWORD: str | None = None
REDIS_SOCKET_TIMEOUT_SEG: float = 2.0
# Pub/Sub bidireccional (ver docs/redis_reunion_node.md).
REDIS_CANAL_COMANDOS: str = "vox:reunion:comandos"  # Python → Node
REDIS_CANAL_RESPUESTAS: str = "vox:reunion:respuestas"  # Node → Python
# Clave con el último comando publicado (GET de respaldo para Node).
REDIS_CLAVE_ULTIMA_SOLICITUD: str = "vox:reunion:ultima_solicitud"
# Clave por solicitud: vox:reunion:respuesta:<solicitud_id> (Node SET, Python GET).
REDIS_CLAVE_RESPUESTA_PREFIJO: str = "vox:reunion:respuesta:"
REDIS_SOLICITUD_TTL_SEG: int = 3600
REDIS_RESPUESTA_TTL_SEG: int = 120
# Tiempo máximo esperando respuesta de Node tras ``iniciar_reunion``.
REDIS_RESPUESTA_TIMEOUT_SEG: float = 5.0
