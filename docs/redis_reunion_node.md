# Redis: reunión Python ↔ Node.js (bidireccional)

Comunicación en la misma Raspberry entre el asistente de voz (Python) y el flujo Node (portal vía WebSockets).

| Dirección | Canal Pub/Sub | Uso |
|-----------|---------------|-----|
| **Python → Node** | `vox:reunion:comandos` | Orden de crear reunión |
| **Node → Python** | `vox:reunion:respuestas` | Estatus de la operación |

Configuración: `src/voice_assistant/config.py` (`REDIS_CANAL_COMANDOS`, `REDIS_CANAL_RESPUESTAS`, `REDIS_RESPUESTA_TIMEOUT_SEG` = **5 s**).

---

## Flujo completo

1. Usuario completa `nueva_reunion` (nombre por voz).
2. Python se suscribe a **respuestas**, publica comando en **comandos**.
3. Node recibe `iniciar_reunion`, inicia sesión y crea reunión en el portal (WebSockets).
4. Node publica `respuesta_iniciar_reunion` en **respuestas** (y opcionalmente `SET` en clave por `solicitud_id`).
5. Python espera hasta **5 s**:
   - Si `estado === "exito"` → reproduce `audio_messages/new_reunion.wav`.
   - Si error o timeout → mensaje en consola, **sin** audio de éxito.

---

## 1. Comando Python → Node

**Canal:** `vox:reunion:comandos` (`REDIS_CANAL_COMANDOS`)

**Clave de respaldo (último comando):** `vox:reunion:ultima_solicitud`

```json
{
  "evento": "iniciar_reunion",
  "solicitud_id": "550e8400-e29b-41d4-a716-446655440000",
  "nombre_reunion": "Revisión sprint",
  "transcripcion": "revisión sprint",
  "timestamp": "2026-05-29T14:32:01.123456+00:00",
  "origen": "voice-assistant"
}
```

Node debe suscribirse a `vox:reunion:comandos` al arrancar.

---

## 2. Respuesta Node → Python

**Canal:** `vox:reunion:respuestas` (`REDIS_CANAL_RESPUESTAS`)

**Clave por solicitud (recomendado además del PUBLISH):**  
`vox:reunion:respuesta:<solicitud_id>`  
TTL sugerido: `REDIS_RESPUESTA_TTL_SEG` (120 s por defecto).

```json
{
  "evento": "respuesta_iniciar_reunion",
  "solicitud_id": "550e8400-e29b-41d4-a716-446655440000",
  "estado": "exito",
  "mensaje": "Reunión creada en el portal",
  "detalle": {},
  "timestamp": "2026-05-29T14:32:04.500000+00:00",
  "origen": "node-reunion"
}
```

### Valores de `estado`

| `estado` | Significado | Python |
|----------|-------------|--------|
| `exito` | Sesión OK y reunión creada | Reproduce audio de éxito |
| `error` | Fallo genérico | Solo consola |
| `error_sesion` | No pudo iniciar sesión en el portal | Solo consola |
| `error_reunion` | Sesión OK pero falló crear la reunión | Solo consola |
| `error_conexion` | WebSocket / red / portal inalcanzable | Solo consola |
| `error_timeout` | Timeout interno de Node (portal lento) | Solo consola |

**Importante:** copiar el mismo `solicitud_id` del comando. Python ignora respuestas de otros ids.

Si Python no recibe nada en **5 s**, trata como timeout local (sin audio de éxito).

---

## Ejemplo Node.js (`redis` v4+)

```javascript
import { createClient } from "redis";

const REDIS_URL = "redis://127.0.0.1:6379";
const CANAL_COMANDOS = "vox:reunion:comandos";
const CANAL_RESPUESTAS = "vox:reunion:respuestas";
const PREFIJO_RESPUESTA = "vox:reunion:respuesta:";
const TTL_RESPUESTA_SEG = 120;

async function publicarRespuesta(cmd, estado, mensaje, detalle = {}) {
  const payload = {
    evento: "respuesta_iniciar_reunion",
    solicitud_id: cmd.solicitud_id,
    estado,
    mensaje,
    detalle,
    timestamp: new Date().toISOString(),
    origen: "node-reunion",
  };
  const body = JSON.stringify(payload);
  const pub = createClient({ url: REDIS_URL });
  await pub.connect();
  await pub.publish(CANAL_RESPUESTAS, body);
  await pub.setEx(`${PREFIJO_RESPUESTA}${cmd.solicitud_id}`, TTL_RESPUESTA_SEG, body);
  await pub.quit();
}

async function procesarIniciarReunion(cmd) {
  try {
    // 1. Iniciar sesión en el portal (WebSockets)
    // 2. Crear reunión con cmd.nombre_reunion
    const ok = await tuLogicaPortal(cmd.nombre_reunion);
    if (ok) {
      await publicarRespuesta(cmd, "exito", "Reunión creada en el portal");
    } else {
      await publicarRespuesta(cmd, "error_reunion", "No se pudo crear la reunión");
    }
  } catch (err) {
    const msg = err?.message ?? String(err);
    const estado = msg.includes("sesion") ? "error_sesion" : "error_conexion";
    await publicarRespuesta(cmd, estado, msg);
  }
}

async function main() {
  const sub = createClient({ url: REDIS_URL });
  await sub.connect();
  await sub.subscribe(CANAL_COMANDOS, async (mensaje) => {
    const cmd = JSON.parse(mensaje);
    if (cmd.evento === "iniciar_reunion") {
      await procesarIniciarReunion(cmd);
    }
  });
  console.log("Node escuchando", CANAL_COMANDOS);
}

main().catch(console.error);
```

Responde **antes de 5 s** desde que Python publicó el comando, o el asistente dará por timeout.

---

## Prueba manual (simular Node)

Terminal 1 — escuchar comandos:

```bash
redis-cli SUBSCRIBE vox:reunion:comandos
```

Terminal 2 — publicar comando (como Python) y simular respuesta exitosa:

```bash
SID=$(python -c "import uuid; print(uuid.uuid4())")
redis-cli PUBLISH vox:reunion:comandos "{\"evento\":\"iniciar_reunion\",\"solicitud_id\":\"$SID\",\"nombre_reunion\":\"Test\",\"transcripcion\":\"Test\",\"timestamp\":\"\",\"origen\":\"voice-assistant\"}"
redis-cli PUBLISH vox:reunion:respuestas "{\"evento\":\"respuesta_iniciar_reunion\",\"solicitud_id\":\"$SID\",\"estado\":\"exito\",\"mensaje\":\"OK\",\"detalle\":{},\"timestamp\":\"\",\"origen\":\"node-reunion\"}"
```

Prueba Python (espera respuesta):

```bash
python -c "
from voice_assistant.integrations import publicar_y_esperar_respuesta_iniciar_reunion
r = publicar_y_esperar_respuesta_iniciar_reunion('Prueba')
print(r.exito, r.respuesta)
"
```

---

## Migración desde nombres antiguos

| Antes | Ahora |
|-------|--------|
| `vox:reunion:eventos` | `vox:reunion:comandos` |
| (no existía) | `vox:reunion:respuestas` |

Actualiza las suscripciones de Node al arrancar.

---

## Documentación relacionada

- [Funciones post-wakeup](funciones_post_wakeup.md)
- [Micrófono compartido ALSA](alsa_mic_compartido.md)
