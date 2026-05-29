# Wake word en Raspberry Pi: saturación de CPU y congelamiento

Este documento describe un problema real observado al ejecutar la detección de wake word (openWakeWord) en la Raspberry Pi, su causa en el código y la solución aplicada en el proyecto.

**Código afectado:** `src/voice_assistant/wake/openwakeword_stream.py`  
**Clase principal de la corrección:** `_EstadoWakeStream`  
**Comandos que usan este camino:** `python main.py --wake-listen`, `python main.py --wake-turn`

---

## Síntomas

- Tras unos **minutos** de escucha continua (`--wake-listen` o espera larga en `--wake-turn`), la detección de wake word **empeoraba** o dejaba de responder.
- La **CPU** del hilo de audio subía de forma sostenida; en casos graves la Raspberry parecía **congelarse** o el audio del micrófono se “comía” (xruns / cortes en PortAudio).
- El problema no era solo el modelo ONNX en sí, sino **cómo se acumulaba el audio en cada callback** del stream de `sounddevice`.

---

## Contexto: por qué el callback es crítico

PortAudio llama a un **callback** cada pocos milisegundos con un bloque nuevo de muestras del micrófono. Ese callback debe:

1. Terminar **rápido** (microsegundos o pocos miliseguros).
2. No bloquear (no hacer inferencia pesada ni copias enormes de memoria).

openWakeWord espera ventanas de **1280 muestras** a 16 kHz (~**80 ms** de audio) en PCM int16. El micrófono puede entregar bloques más pequeños o a otra tasa (p. ej. 48 kHz), así que hace falta **acumular** fragmentos hasta armar cada ventana de 80 ms.

El error de diseño original era **cómo** se acumulaba ese audio.

---

## El problema: `np.concatenate` sobre todo el buffer en cada callback

### Patrón problemático (conceptual)

En la versión que degradaba el sistema, el flujo era equivalente a:

```python
# ANTI-PATRÓN (no usar en el callback de audio en tiempo real)
pending = np.concatenate([pending, nuevo_bloque_del_mic])
# ... extraer ventanas de 80 ms desde pending ...
```

En **cada** invocación del callback:

1. Se creaba un array **nuevo** que copiaba **todo** el audio pendiente más el bloque recién llegado.
2. El tamaño de `pending` crecía si la inferencia iba un poco más lenta que la captura.
3. El coste de cada `concatenate` es **O(n)** respecto al tamaño del buffer acumulado.

### Por qué empeora con el tiempo

| Factor | Efecto |
|--------|--------|
| Frecuencia del callback | Decenas de veces por segundo (p. ej. cada 32–80 ms). |
| Coste por callback | Proporcional al **tamaño total** del buffer pendiente, no solo al bloque nuevo. |
| Inferencia ONNX en el mismo hilo (antes) | El callback tardaba más → más audio pendiente → buffers más grandes → más CPU en el siguiente callback. |
| Raspberry Pi | Menos margen de CPU; el hilo de audio compite con el sistema y con la inferencia. |

Resultado: un **ciclo de retroalimentación**: más retraso → más buffer → más copias → más CPU → peor audio → peor wake → sensación de congelamiento.

Eso encaja con el diagnóstico: *“copias crecientes y puede saturar la CPU del hilo de audio, degradando la captura”*.

---

## La solución (dos partes)

### 1. Cola de fragmentos con `deque` (`_EstadoWakeStream`)

En lugar de un único array que se reconcatena entero, el audio pendiente es una **cola de fragmentos** (`collections.deque` de arrays `float32` mono):

- Cada callback **solo hace `append`** del bloque nuevo (coste acotado al tamaño del bloque).
- Para formar una ventana de ~80 ms, se consumen fragmentos desde el frente (`popleft` o recorte del primero), sin copiar todo el historial en cada tick.
- Solo se usa `np.concatenate` cuando hace falta **unir 2+ fragmentos** para completar **una** ventana (tamaño acotado ≈ `need` muestras), no para todo el backlog histórico.

**Límite de cola:** si hay más de `need * 40` muestras pendientes (~3,2 s a 16 kHz equivalente), se descartan los fragmentos **más antiguos**. Así la memoria y el trabajo por callback no crecen sin tope si la inferencia se atrasa.

Implementación: clase `_EstadoWakeStream` y método `extraer_bloques_pcm16()` en `openwakeword_stream.py`.

```text
Callback PortAudio
    → append fragmento al deque
    → (opcional) descartar audio muy antiguo si la cola es larga
    → mientras haya muestras suficientes: emitir ventana 1280 @ 16 kHz (int16)
    → encolar ventana para inferencia (no inferir aquí)
```

### 2. Inferencia en un hilo separado y cola acotada

La segunda parte mueve **`modelo.predict(pcm)`** fuera del callback:

- El callback solo **encola** ventanas PCM en `infer_queue` (tamaño máximo 16).
- Un hilo daemon (`openWakeWord-infer` o `openWakeWord-infer-once`) consume la cola y ejecuta ONNX.
- Si la cola está llena, se **descarta la ventana más antigua** y se encola la nueva (priorizar audio reciente).

Beneficios:

- El callback vuelve a ser liviano (append + encolar).
- Un pico de CPU de ONNX no bloquea PortAudio durante decenas de milisegundos en el hilo de audio.
- Menos xruns y menos sensación de “wake muerto” tras minutos de escucha.

Misma arquitectura en `ejecutar_escucha_openwakeword` y `esperar_primera_activacion_wake`.

---

## Comparación resumida

| Aspecto | Antes (problemático) | Después (actual) |
|---------|----------------------|------------------|
| Acumulación de audio | `concatenate` de **todo** el pending en cada callback | `deque` de fragmentos; concat solo para **una** ventana |
| Complejidad por callback | O(tamaño del backlog), crece con el tiempo | O(tamaño del bloque entrante + ventanas emitidas), backlog acotado |
| Inferencia wake | En el callback (o bloqueando el camino crítico) | Hilo dedicado + `queue.Queue` acotada |
| Comportamiento en Pi tras minutos | Degradación / congelamiento | Escucha estable en pruebas prolongadas |

---

## Dónde leer el código hoy

| Elemento | Ubicación |
|----------|-----------|
| Documentación en cabecera del módulo | Comentario “Flujo de datos” al inicio de `openwakeword_stream.py` |
| Buffer con `deque` | `_EstadoWakeStream` (~líneas 142–200) |
| Cola + hilo de inferencia (escucha larga) | `ejecutar_escucha_openwakeword` |
| Cola + hilo (un solo wake, `--wake-turn`) | `esperar_primera_activacion_wake` |

---

## Ajustes relacionados (no sustitutos del fix anterior)

Estos cambios **complementan** la corrección de rendimiento; no reemplazan el `deque` ni el hilo de inferencia:

- **Umbral y rebote** (`OPENWAKEWORD_UMBRAL`, `OPENWAKEWORD_REBOTE_SEG`): evitan spam de detecciones, no arreglan CPU por sí solos.
- **Audio de confirmación en bloqueante** tras detectar wake: evita solapar beep con la grabación de la orden; no reduce carga del stream de wake.
- **Modelo ONNX custom** en `OPENWAKEWORD_MODELOS`: define *qué* se detecta; el patrón de buffer afecta *cómo de bien* corre el stream en la Pi.

---

## Si vuelve a degradarse el wake

1. Confirmar que no se reintrodujo `concatenate` del buffer completo en el callback.
2. Revisar que la inferencia siga en hilo separado y que la cola tenga tope.
3. Probar con `--wake-listen 0` varios minutos y observar CPU (`top`, `htop`).
4. Valorar modelo más pequeño o `blocksize` del stream si el cuello de botella pasa a ser solo ONNX.

---

## Documentación relacionada

- [Turno completo `--wake-turn`](wake_turn.md) — pipeline que usa `esperar_primera_activacion_wake`.
- Comentarios en `_EstadoWakeStream.extraer_bloques_pcm16` y en los hilos `_hilo_inferencia*`.
