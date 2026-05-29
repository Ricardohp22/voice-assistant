# Redis: solicitud de nueva reunión (Python → Node.js)

El asistente de voz publica en Redis cuando el usuario completa el flujo `nueva_reunion` (nombre capturado por micrófono + STT). Un proceso **Node.js** en la misma Raspberry puede reaccionar al instante vía **Pub/Sub** o leer la última solicitud con **GET**.

Configuración Python: `src/voice_assistant/config.py` (`REDIS_*`).  
Código que publica: `src/voice_assistant/integrations/redis_reunion.py`, llamado desde `manejar_nueva_reunion` en `manejadores.py`.

---

## Flujo en el dispositivo

1. Usuario dispara intención `nueva_reunion`.
2. Se reproduce `ask_name.wav` y se graban `NUEVA_REUNION_ESCUCHA_SEG` s (p. ej. 5 s).
3. Whisper transcribe → **nombre de la reunión**.
4. Python escribe en Redis (clave + `PUBLISH`).
5. Se reproduce `new_reunion.wav` (confirmación al usuario).

Node debería **suscribirse al canal** al arrancar y crear la reunión en cuanto llegue el mensaje.

---

## Canal Pub/Sub (recomendado para reacción inmediata)

| Parámetro | Valor por defecto |
|-----------|-------------------|
| Canal | `vox:reunion:eventos` (`REDIS_CANAL_REUNION_EVENTOS`) |

Cada solicitud es un mensaje **JSON en UTF-8** (string).

### Esquema del payload

```json
{
  "evento": "iniciar_reunion",
  "solicitud_id": "550e8400-e29b-41d4-a716-446655440000",
  "nombre_reunion": "Revisión sprint",
  "transcripcion": "revisión sprint",
  "timestamp": "2026-05-19T14:32:01.123456+00:00",
  "origen": "voice-assistant"
}
```

| Campo | Descripción |
|-------|-------------|
| `evento` | Siempre `iniciar_reunion` por ahora. |
| `solicitud_id` | UUID único por solicitud (idempotencia / logs). |
| `nombre_reunion` | Texto limpio usado como nombre (trim). |
| `transcripcion` | Texto crudo de STT (puede coincidir con `nombre_reunion`). |
| `timestamp` | ISO 8601 UTC. |
| `origen` | `voice-assistant`. |

Filtra en Node: `if (payload.evento === 'iniciar_reunion') { ... crear reunión con payload.nombre_reunion }`.

---

## Clave de respaldo (GET)

| Parámetro | Valor por defecto |
|-----------|-------------------|
| Clave | `vox:reunion:ultima_solicitud` (`REDIS_CLAVE_ULTIMA_SOLICITUD`) |
| TTL | 3600 s (`REDIS_SOLICITUD_TTL_SEG`; `0` = sin caducidad) |

Mismo JSON que en Pub/Sub. Útil si Node arranca después del evento o pierde un mensaje.

---

## Ejemplo Node.js (`redis` v4+)

```javascript
import { createClient } from "redis";

const REDIS_URL = "redis://127.0.0.1:6379";
const CANAL = "vox:reunion:eventos";
const CLAVE_ULTIMA = "vox:reunion:ultima_solicitud";

async function crearReunionDesdeVoz(payload) {
  console.log("Crear reunión:", payload.nombre_reunion, payload.solicitud_id);
  // Tu lógica: API, base de datos, etc.
}

async function main() {
  const sub = createClient({ url: REDIS_URL });
  const cmd = createClient({ url: REDIS_URL });
  await sub.connect();
  await cmd.connect();

  // Opcional: procesar la última solicitud si ya existía al arrancar
  const previa = await cmd.get(CLAVE_ULTIMA);
  if (previa) {
    await crearReunionDesdeVoz(JSON.parse(previa));
  }

  await sub.subscribe(CANAL, (mensaje) => {
    const payload = JSON.parse(mensaje);
    if (payload.evento === "iniciar_reunion") {
      crearReunionDesdeVoz(payload);
    }
  });

  console.log("Escuchando", CANAL);
}

main().catch(console.error);
```

---

## Conexión Redis

Por defecto el asistente usa:

- Host: `127.0.0.1`
- Puerto: `6379`
- DB: `0`
- Sin contraseña

Ajusta en `config.py` si tu instalación difiere. Desactiva publicación con `REDIS_HABILITADO = False`.

---

## Prueba sin micrófono

Con Redis en marcha (`redis-cli ping` → `PONG`):

```bash
pip install redis>=5.0.0
python main.py --test-oracion "Hey vox device crea una nueva reunion"
```

(Solo ejecutará el manejador si el emparejo coincide; la grabación del nombre requiere el flujo completo con audio.)

Prueba directa desde Python:

```bash
python -c "
from voice_assistant.integrations import publicar_solicitud_iniciar_reunion
print(publicar_solicitud_iniciar_reunion('Reunión de prueba'))
"
```

En otra terminal:

```bash
redis-cli SUBSCRIBE vox:reunion:eventos
# o
redis-cli GET vox:reunion:ultima_solicitud
```

---

## Evolución futura

Se pueden añadir otros `evento` en el mismo canal (`cancelar_reunion`, `unirse_reunion`, …) o claves por `solicitud_id` (`vox:reunion:solicitud:<uuid>`) sin cambiar el contrato mínimo actual.
