## AI Light Canvas (MVP)

Local MVP for an AI-ready LED wall art system with a browser simulator. The goal is a **generative art engine** that can later target real hardware via display adapters, not “just a web animation”.

### What you get
- **FastAPI backend** that generates hardware-ready frames and streams them over **WebSocket**
- **Plain HTML/CSS/JS frontend** that renders a physical-feeling LED grid on an HTML canvas
- **Runtime settings** editable in a settings panel (matrix size, FPS, brightness, speed, LED look)
- Non-sensitive defaults in `config/settings.yaml`, optional secrets via `.env` (not required for MVP)

### Frame format
Frames are streamed as JSON shaped like:

```json
{
  "width": 64,
  "height": 64,
  "pixels": [
    [[r,g,b], [r,g,b]],
    [[r,g,b], [r,g,b]]
  ]
}
```

### Run (WSL recommended)
From the project root (this `artmaker/` folder):

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open from Windows browser:
- `http://localhost:8000/`

### Run (Windows Python)
From this `artmaker\` folder:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open:
- `http://127.0.0.1:8000/`

### Settings and secrets
- Local runtime settings (not committed): `config/settings.yaml`
- Checked-in example: `config/settings.example.yaml`
- Optional secrets: copy `.env.example` to `.env` and fill later (MVP does not require any secrets).

### Next logical improvements (after MVP is stable)
- Add more patterns (particles, trails, fade/redraw, palette modes)
- Switch frame transport to binary (packed RGB) for higher FPS / larger matrices
- Add preset saving/loading and a “scene” concept
- Add additional display adapters (e.g., Raspberry Pi matrix, ESP32) without changing the art engine

