# Funciones tras el wakeup (catálogo de intenciones)

Este documento describe **cómo se detecta** una intención y **dónde se implementa** lo que hace el dispositivo.

Para el pipeline completo (`--wake-turn`), véase [Turno completo: `--wake-turn`](wake_turn.md).

## Separación de responsabilidades

| Capa | Archivo | Rol |
|------|---------|-----|
| **Identificación** | `data/catalogo_intenciones.json` | Prefijos de wake, `id`, disparadores, prioridad. **Sin audios ni lógica de ejecución.** |
| **Emparejo** | `src/voice_assistant/intents/catalogo.py` | Normaliza texto, quita wake, elige intención por disparadores. |
| **Ejecución** | `src/voice_assistant/intents/manejadores.py` | Una función por `id`; reproduce audio, imprime consola, llama APIs, etc. |

En `config.py`, `CATALOGO_INTENCIONES_RUTA` apunta al JSON (ruta relativa a la **raíz del repositorio**).

## Flujo resumido

1. **Wakeup** (openWakeWord).
2. **STT** de la orden (Whisper en `--wake-turn`).
3. **Normalización** y quitar prefijos de wake.
4. **Emparejar** intención en el JSON → obtienes un `id` (p. ej. `nueva_reunion`).
5. **`ejecutar_intencion(id)`** → llama al manejador registrado en código.

## Esquema del JSON (`version` ≥ 1)

### Raíz

| Campo | Tipo | Descripción |
|--------|------|-------------|
| `version` | entero | Versión del esquema del archivo. |
| `prefijos_wake` | lista de strings | Formas del wake al inicio de la frase (tras normalizar). |
| `intenciones` | lista de objetos | Definiciones de intención (solo identificación). |

### Cada intención

| Campo | Tipo | Descripción |
|--------|------|-------------|
| `id` | string | Identificador estable; debe existir en `REGISTRO_MANEJADORES`. |
| `titulo` | string | Nombre corto para logs. |
| `descripcion` | string | (Opcional) Documentación humana. |
| `prioridad` | entero | Mayor = se evalúa antes en empates. |
| `disparadores` | objeto | Reglas locales; hoy `contiene_alguna`. |

**No incluir** en el JSON: rutas de WAV, mensajes de consola, tipos de acción, URLs, etc.

### Disparadores

- **`contiene_alguna`**: si alguna subcadena aparece en el texto (sin wake, normalizado), la intención es candidata. Gana la subcadena **más larga**; empate por **mayor `prioridad`**.

## Intenciones actuales (ejecución en código)

| `id` | Comportamiento (`manejadores.py`) |
|------|-----------------------------------|
| `saludar` | Reproduce `audio_messages/saludo.wav`. |
| `nueva_reunion` | Pide nombre (`ask_name.wav`), escucha y transcribe, publica en Redis (`iniciar_reunion`), reproduce `new_reunion.wav`. Ver [Redis → Node](redis_reunion_node.md). |

## Añadir una intención nueva

1. **JSON**: objeto en `intenciones` con `id`, `titulo`, `prioridad`, `disparadores`.
2. **Código**: en `manejadores.py`, función `manejar_<id>(*, bloqueante=True)` con la lógica deseada.
3. **Registro**: añadir `"<id>": manejar_<id>` en `REGISTRO_MANEJADORES`.

Si el JSON define un `id` sin manejador, `ejecutar_intencion` lanzará `ValueError` con un mensaje claro.

## Prueba local sin micrófono

```bash
python main.py --test-oracion "Hex vox device hola"
python main.py --test-oracion "Hey vox device crea una nueva reunion"
python main.py --list-intents
```

## Dudas frecuentes

- **¿Dónde pongo nuevas frases?** En `disparadores.contiene_alguna` del JSON.
- **¿Dónde pongo el audio o la lógica?** En `manejadores.py` (y rutas bajo `audio_messages/` si aplica).
- **¿Y si el STT transcribe mal?** Añade variantes en el JSON o sube `prioridad` / disparadores más largos.
