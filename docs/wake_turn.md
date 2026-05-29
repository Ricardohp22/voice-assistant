# Turno completo: `python main.py --wake-turn`

Este documento describe el **algoritmo** del comando `--wake-turn` (wake → grabación → STT local → intención → acción) y **cómo probarlo** de forma fiable en el dispositivo.

## Objetivo del comando

`--wake-turn` ejecuta **un solo ciclo** de interacción:

1. Esperar hasta que openWakeWord detecte la wake word (o hasta un tiempo máximo).
2. Tras la detección, dejar pasar un breve silencio y **grabar** la orden del usuario.
3. Normalizar el audio a **mono 16 kHz** (contrato del STT).
4. **Transcribir** con Whisper local (`faster-whisper`).
5. **Emparejar** el texto contra el catálogo JSON de intenciones y **ejecutar** la acción (por ejemplo, reproducir `audio_messages/saludo.wav` para la intención `saludar`).

El código de orquestación está en `src/voice_assistant/pipeline/turno_wake_stt_intent.py` (`ejecutar_turno_wake_grabar_stt_intent`). La CLI solo resuelve el micrófono y pasa parámetros desde `config.py`.

## Fases del algoritmo (en orden)

### 1. Espera de la primera activación de wake

Se llama a `esperar_primera_activacion_wake` en `src/voice_assistant/wake/openwakeword_stream.py`.

- Se abre un **stream de entrada** con `sounddevice` a la tasa que el hardware acepte (objetivo: la de `config.TASA_MUESTREO_HZ`, p. ej. 16 kHz). Si la tasa efectiva difiere, el audio se **remuestrea a 16 kHz** antes de alimentar openWakeWord.
- En cada callback de captura, el audio se convierte a **mono float32** y se encola en un estado interno basado en **`deque`**: se van extrayendo ventanas equivalentes a **~80 ms a 16 kHz** (1280 muestras en PCM int16), que es lo que espera el modelo. Así se evita concatenar todo el buffer histórico en cada callback (importante en Raspberry para no degradar la detección con el tiempo).
- Cada ventana se envía a una **cola acotada** consumida por un **hilo de inferencia**. El hilo llama a `modelo.predict(pcm)` y, para cada clave/score, aplica:
  - umbral mínimo (`config.OPENWAKEWORD_UMBRAL`);
  - **rebote** (`config.OPENWAKEWORD_REBOTE_SEG`): no cuenta dos disparos demasiado seguidos.
- El bucle principal espera hasta **la primera** activación válida o hasta que venza `config.WAKE_TURN_TIMEOUT_SEG`.
- Si en `config` hay ruta de **audio de confirmación** (`OPENWAKEWORD_AUDIO_CONFIRMACION`) y el archivo existe, se reproduce con `sounddevice` en modo **bloqueante** antes de seguir. Así la grabación de la orden no se solapa con el “beep” de confirmación.
- La cadena `config.FRASE_ACTIVACION` se pasa por compatibilidad de API con otras rutas de wake; **openWakeWord no interpreta esa frase**: solo ejecuta los modelos listados en `OPENWAKEWORD_MODELOS`.

### 2. Pausa post-wake

`time.sleep(config.POST_WAKE_SILENCIO_SEG)` reduce solapamiento residual con el audio de confirmación o con el final de la palabra de wake.

### 3. Grabación de la orden

`grabar_muestras` graba durante `config.POST_WAKE_GRABAR_ORDEN_SEG` segundos por el mismo dispositivo de entrada.

### 4. Preparación para STT

`preparar_muestras_para_stt` deja el audio en **mono**, tasa `config.TASA_SALIDA_PIPELINE_HZ` (habitualmente 16 kHz) y formato adecuado para Whisper.

### 5. (Opcional) WAV de depuración

Si `config.WAKE_TURN_GUARDAR_WAV_DEBUG` es `True`, se guarda un WAV con timestamp en `config.CARPETA_GRABACIONES` para revisar con `aplay` u otra herramienta si el micrófono captó bien la orden.

### 6. Transcripción local

`transcribir_float32_16khz` (`src/voice_assistant/stt/`) usa **faster-whisper** con `WHISPER_MODELO`, `WHISPER_DISPOSITIVO`, `WHISPER_TIPO_COMPUTO` y `WHISPER_IDIOMA` de `config.py`.

### 7. Intención y acción

- Se carga `data/catalogo_intenciones.json` (ruta `config.CATALOGO_INTENCIONES_RUTA`).
- El texto se **normaliza** (minúsculas, sin acentos, espacios compactados) y se intentan quitar los **prefijos de wake** definidos en `prefijos_wake` del JSON. Luego se busca la intención cuyos disparadores contengan fragmentos del texto (ver `docs/funciones_post_wakeup.md`).
- Si hay coincidencia, se ejecuta la acción (p. ej. `reproducir_audio`).

Si no hay wake a tiempo, no hay intención coincidente o falla algún paso intermedio, el programa imprime un mensaje claro y termina ese turno (no entra en bucle infinito salvo que vuelvas a lanzar el comando).

## Parámetros relevantes en `config.py`

| Constante | Rol |
|-----------|-----|
| `MIC_NOMBRE_CONTIENE`, `DISPOSITIVO_ENTRADA` | Selección del micrófono (igual que en otros comandos). |
| `OPENWAKEWORD_*` | Modelos, umbral, rebote, inferencia, VAD, bloque de stream, audio de confirmación. |
| `WAKE_TURN_TIMEOUT_SEG` | Tiempo máximo esperando la **primera** detección de wake. |
| `POST_WAKE_SILENCIO_SEG` | Pausa tras el wake (y tras el beep) antes de grabar. |
| `POST_WAKE_GRABAR_ORDEN_SEG` | Duración de la ventana de grabación de la orden. |
| `WAKE_TURN_GUARDAR_WAV_DEBUG` | Guardar WAV de la orden tras normalizar. |
| `WHISPER_*` | Modelo, dispositivo (`cpu`), tipo de cómputo (`int8` en CPU), idioma. |
| `CATALOGO_INTENCIONES_RUTA` | Catálogo de intenciones. |

## Cómo realizar la prueba

### Preparación del entorno

1. Activar el entorno virtual del proyecto e instalar dependencias (incluido el paquete en modo editable para que `voice_assistant` sea importable):

   ```bash
   cd voice-assistant
   source venv/bin/activate
   pip install -r requirements.txt
   pip install -e .
   ```

2. Comprobar que existen el modelo de wake referenciado en `OPENWAKEWORD_MODELOS`, el WAV de confirmación (si se usa) y los WAV de acciones del catálogo.

### Comprobar micrófono antes del turno completo

1. Listar dispositivos y anotar el índice o el nombre:

   ```bash
   python main.py --list-devices
   ```

2. Ajustar en `config.py` `MIC_NOMBRE_CONTIENE` o `DISPOSITIVO_ENTRADA` y verificar que hay datos válidos:

   ```bash
   python main.py --check-input-device
   ```

### Ejecutar un turno

```bash
python main.py --wake-turn
```

Secuencia recomendada al hablar:

1. **Pronunciar la wake word** que corresponda al modelo ONNX configurado (no la frase de `FRASE_ACTIVACION` a menos que coincida con lo que realmente dispara el modelo).
2. Esperar el **sonido de confirmación** (si está configurado).
3. Dentro de la ventana de grabación (`POST_WAKE_GRABAR_ORDEN_SEG`), decir una **orden corta** que coincida con el catálogo; por ejemplo, para la intención de ejemplo `saludar`: “hola”, “saluda”, “qué tal”, etc. Si Whisper incluye al inicio algo parecido a “hey vox device”, los `prefijos_wake` del JSON ayudan a quitarlo antes del emparejo.

### Si algo falla

- **Timeout sin wake**: bajar umbral con cuidado, revisar ruido, modelo incorrecto o micrófono mal resuelto.
- **Wake OK pero sin intención**: revisar en consola la línea `STT: '...'`; ampliar disparadores o `prefijos_wake` en `data/catalogo_intenciones.json` para alinearlos con la salida real de Whisper.
- **Dudas sobre el audio de la orden**: poner `WAKE_TURN_GUARDAR_WAV_DEBUG = True`, repetir el turno y escuchar el WAV generado.

## Documentación relacionada

- [Wake word: saturación de CPU en Raspberry y solución (`deque` + hilo de inferencia)](wake_word_rendimiento_raspberry.md)
- [Funciones tras el wakeup y catálogo JSON](funciones_post_wakeup.md)
