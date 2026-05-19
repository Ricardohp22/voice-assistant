# Funciones tras el wakeup (catálogo de intenciones)

Este documento describe **qué puede hacer el dispositivo** después de detectar la wake word, **cómo se decide** la acción y **dónde editarlo**. El archivo que realmente consume el código es el **catálogo JSON**; este Markdown sirve como guía humana.

Para el **pipeline completo en un solo comando** (wake → grabar → Whisper → catálogo → acción), véase [Turno completo: `--wake-turn`](wake_turn.md).

## Flujo resumido

1. **Wakeup** (openWakeWord u otro motor).
2. **Oración del usuario** (más adelante: STT local/cloud). Hoy se puede simular con `python main.py --test-oracion "..."`.
3. **Normalización** del texto (minúsculas, sin acentos, espacios compactados).
4. **Quitar prefijos de wake** (variantes de “hey vox device”, “hex vox device”, etc.) del inicio de la frase.
5. **Emparejar intención** según disparadores del catálogo (reglas locales).
6. **Ejecutar acción** asociada (p. ej. reproducir un WAV).

## Archivo maestro (máquina + humanos)

| Ruta | Rol |
|------|-----|
| `data/catalogo_intenciones.json` | Catálogo versionado: prefijos de wake, intenciones, disparadores y acciones. |
| `src/voice_assistant/intents/` | Código: carga JSON, empareja y ejecuta acciones soportadas. |

En `config.py`, `CATALOGO_INTENCIONES_RUTA` apunta al JSON (ruta relativa a la **raíz del repositorio**).

## Esquema del JSON (`version` ≥ 1)

### Raíz

| Campo | Tipo | Descripción |
|--------|------|-------------|
| `version` | entero | Versión del esquema del archivo. |
| `prefijos_wake` | lista de strings | Formas reconocidas del wake al inicio de la frase (tras normalizar). Ej.: “hex vox device”. |
| `intenciones` | lista de objetos | Cada uno es una función lógica del asistente. |

### Cada intención

| Campo | Tipo | Descripción |
|--------|------|-------------|
| `id` | string | Identificador estable (`saludar`, `clima`, …). |
| `titulo` | string | Nombre corto para logs/UI. |
| `descripcion` | string | (Opcional) Texto para documentación. |
| `prioridad` | entero | Mayor = se evalúa antes si varias podrían coincidir. |
| `disparadores` | objeto | Reglas locales; hoy se usa `contiene_alguna`. |
| `accion` | objeto | Qué ejecutar si hay coincidencia. |

### Disparadores (fase actual)

- **`contiene_alguna`**: lista de subcadenas. Si **alguna** aparece en el texto ya sin wake (normalizado), la intención **candidata**. Se elige la coincidencia con **subcadena más larga**; empate por **mayor `prioridad`**.

Limitación conocida: subcadenas muy cortas pueden dar falsos positivos; conviene frases más específicas en intenciones sensibles.

### Acciones soportadas (`accion.tipo`)

| `tipo` | `parametros` | Comportamiento |
|--------|----------------|------------------|
| `reproducir_audio` | `ruta` (string, relativa a la raíz del repo) | Reproduce el WAV por el dispositivo de salida por defecto (`sounddevice`). |

Más tipos (`http_get`, `gpio`, `mqtt`, …) se pueden añadir en el mismo JSON cuando exista el ejecutor en código.

## Intención de ejemplo: `saludar`

- **Disparadores**: “hola”, “buen día”, “salúdame”, etc. (ver lista en el JSON).
- **Acción**: `audio_messages/saludo.wav`.

## Prueba local sin micrófono

```bash
python main.py --test-oracion "Hex vox device hola"
python main.py --list-intents
```

## Dudas frecuentes

- **¿Dónde pongo nuevas frases?** En `disparadores.contiene_alguna` de la intención correspondiente, o crea otra intención con su propia lista.
- **¿Y si el STT devuelve errores de transcripción?** Añade variantes ortográficas en el JSON o sube la prioridad de intenciones con disparadores más largos/únicos.
- **¿Rutas absolutas?** También válidas en `parametros.ruta`.
