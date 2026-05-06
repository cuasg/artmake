# Raspberry Pi: LED matrix + artmaker

This project can drive **WS281x / NeoPixel-style** strips (single data wire, row-major pixels: `index = y * width + x`). Common matrix panels use **HUB75** (parallel RGB + clock/latch), which is **not** what `rpi_ws281x` speaks—you would need a different stack or a bridge board.

## What you get

- **Desktop / testing**: Settings → frame output **Simulator**, playlist **Selected image** — only the library image you pick is rendered (same as today).
- **On the Pi**: Set frame output to **Raspberry Pi**, pattern **Living drawing**, playlist **Album** — after **Start**, each **full** draw → erase → loop cycle advances to the next library image that has a **vectorized** toolpath for the **current matrix width×height**, then repeats forever.

Album order is **sorted image id** (stable). If only one image qualifies, it keeps showing that one.

## Install (Pi)

1. Raspberry Pi OS (64-bit recommended on Pi 4/5). Enable **SPI** or use the GPIO pin your wiring expects (WS281x often uses PWM-capable pins; follow your HAT/vendor docs).
2. Python 3.11+ if available via apt, or use the OS default.
3. From the repo:

   ```bash
   cd backend
   python3 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements-pi.txt
   ```

4. Run the API — pick one:

   **Manual (good until you enable systemd)** — from the repo root on the Pi:

   ```bash
   chmod +x deploy/start-server.sh
   ./deploy/start-server.sh
   ```

   Optional: `HOST=127.0.0.1 PORT=8080 ./deploy/start-server.sh` binds differently.

   **Equivalent one-liner** (from `backend/` with venv activated):

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000
   ```

   Open the UI from another machine or the Pi browser, set matrix preset to match hardware, wire GPIO settings, then **Start**.

### Auto-start with systemd

Edit paths and user in `deploy/artmaker.service` if your clone is not at `/home/pi/artmaker`, then:

```bash
sudo cp deploy/artmaker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now artmaker.service
journalctl -u artmaker.service -f
```

Stop/disable: `sudo systemctl disable --now artmaker.service`.

## Settings (UI or `config/settings.yaml`)

Under **Raspberry Pi & playlist**:

| Field | Purpose |
| --- | --- |
| Frame output | `simulator` vs `raspberry_pi` |
| Playlist | `selected` = test one image; `album` = cycle after each full living-drawing cycle on the Pi |
| GPIO pin | BCM number for the LED data line (default 18 — verify for your wiring) |
| Strip type | Must match LED order (`WS2812_STRIP`, `WS2811_STRIP_GRB`, etc.) |
| Brightness / RGB gain | Hardware brightness cap and optional post gain |

Pi 4/5 + DMA/PWM details vary by OS and pin—if initialization fails, check the [`rpi_ws281x`](https://github.com/rpi-ws281x/rpi-ws281x-python) README and your pin choice.

## Power

WS281x matrices can draw **amps** at full white. Use an adequate **5 V** supply for the panel, common ground with the Pi, and avoid powering the full matrix from the Pi’s 5 V header alone.
