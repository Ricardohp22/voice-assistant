# Micrófono compartido (ALSA `dsnoop` / `pcm.compartido`)

En la Raspberry, el micrófono USB (`hw:0,0`) solo puede abrirse en **modo exclusivo** si un proceso usa el dispositivo hardware directamente. Para que **Python** (este asistente) y **Node.js** capturen a la vez, se define en `/etc/asound.conf` un PCM de tipo **dsnoop**:

```text
pcm.compartido {
    type dsnoop
    ipc_key 234567
    slave {
        pcm "hw:0,0"
        channels 1
        rate 16000
        format S16_LE
    }
}
```

Ese PCM aparece en ALSA como `compartido` y en PortAudio/sounddevice como dispositivo de entrada **`compartido`** (no usar `UM02: USB Audio (hw:0,0)` si el otro proceso también necesita el mic).

---

## Configuración en este proyecto

En `src/voice_assistant/config.py`:

| Parámetro | Valor recomendado |
|-----------|-------------------|
| `MIC_NOMBRE_CONTIENE` | `"compartido"` |
| `DISPOSITIVO_ENTRADA` | `None` (el nombre tiene prioridad) |

Tras cambiar la config:

```bash
python main.py --list-devices
python main.py --check-input-device
```

Debe resolverse algo como `[11] compartido`, no el índice del `hw:0,0` USB.

---

## Tasa de muestreo (16 kHz vs 44100 Hz)

El `slave.rate 16000` en `asound.conf` es el formato deseado del dsnoop; el hardware USB puede seguir reportando **44100 Hz** a PortAudio. En ese caso verás un aviso y la captura se hará a la tasa nativa; el proyecto **remuestrea a 16 kHz** antes de openWakeWord y Whisper (`preparar_muestras_para_stt` / buffer de wake).

No es un fallo del dsnoop: lo importante para compartir el mic es abrir **`compartido`**, no la tasa exacta en la apertura ALSA.

Si necesitas 16 kHz estrictos en ALSA, puedes envolver el esclavo con `plug` (consulta `arecord` cuando sugiera `-Dplug:compartido`).

---

## Node.js

Abre el **mismo** PCM ALSA, no `hw:0,0`:

- Herramientas CLI: `arecord -D compartido ...`
- Bibliotecas Node: dispositivo de captura equivalente a `compartido` (según el binding ALSA/Pulse que uses).

Si Node abre el hardware directo, el asistente Python puede perder el mic o viceversa.

---

## Comprobaciones rápidas

```bash
arecord -L | grep -A2 compartido
python main.py --list-devices
arecord -D compartido -f S16_LE -c 1 -d 2 /tmp/prueba.wav
```

Con el asistente en marcha, un segundo `arecord -D compartido` en paralelo debería poder grabar (prueba de dsnoop).

---

## Documentación relacionada

- [Turno `--wake-turn`](wake_turn.md)
- [Redis reunión → Node](redis_reunion_node.md)
