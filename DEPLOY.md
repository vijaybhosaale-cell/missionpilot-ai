# Put MissionPilot AI online / MissionPilot AI live कसे करायचे

> मराठी मार्गदर्शक खाली आहे. ([जा](#मराठी-मार्गदर्शक))

## Option A — Render (recommended, about 15 minutes)

Render runs the Docker image in this folder. Chromium and the Noto fonts are inside the image, so PDFs come out correctly in Marathi.

**1. Put the code on GitHub**

```bash
cd missionpilot
git init
git add .
git commit -m "MissionPilot AI v1"
git branch -M main
git remote add origin https://github.com/<your-user>/missionpilot-ai.git
git push -u origin main
```

(Or on github.com: New repository → “uploading an existing file” → drag the *contents* of the folder.)

**2. Create the service on Render**

1. Sign in at https://render.com with GitHub.
2. **New → Blueprint** → pick the `missionpilot-ai` repository. Render reads `render.yaml` and creates:
   - a Docker web service with a health check on `/healthz`,
   - a random `SECRET_KEY`,
   - a 1 GB disk at `/app/data` so projects are kept between restarts.
3. When asked for `ANTHROPIC_API_KEY`, paste a Claude API key to turn on AI writing — or leave it empty (everything still works from the template library).
4. Press **Apply**. The first build takes about 5–8 minutes (it downloads the Playwright image).
5. Open the `https://missionpilot-ai-xxxx.onrender.com` address. Done.

**3. After it is live**

- In the service's **Environment**, set `PUBLIC_URL` to the site address (so share links use it) and keep `HTTPS_ONLY=1`.
- Custom domain: **Settings → Custom Domains → Add**, then add the CNAME record at your domain provider.
- Updates: push to GitHub → Render redeploys automatically.

**About cost and data.** The persistent disk needs a paid instance type (the Blueprint uses `starter`). If you switch the service to the free instance type, remove the `disk:` block from `render.yaml`: the site still works, but the database is wiped whenever the service restarts or sleeps — fine for a demo, not for real projects. Check current prices on render.com before choosing.

## Option B — any server with Docker (VPS, university server, laptop)

```bash
docker build -t missionpilot .
docker run -d --name missionpilot -p 8000:8000 \
  -e SECRET_KEY="$(openssl rand -hex 32)" \
  -e ANTHROPIC_API_KEY="" \
  -v missionpilot-data:/app/data \
  missionpilot
```

Open http://SERVER-IP:8000. Put a reverse proxy with HTTPS (Caddy, Nginx) in front for a public site and set `HTTPS_ONLY=1`.

## Option C — run on your own laptop (Windows)

1. Install Python 3.11 or newer from python.org (tick **“Add python.exe to PATH”**).
2. Double-click `run_local.bat`. The first run installs packages and Chromium (a few minutes).
3. Open http://localhost:8000.

Other devices on the same Wi-Fi/hotspot can open `http://<your-laptop-IP>:8000` (find the IP with `ipconfig`; allow Python through Windows Firewall on private networks).

## Settings (environment variables)

| Variable | Needed | What it does |
|---|---|---|
| `SECRET_KEY` | yes | Signs login cookies. Long random text. Never share it. |
| `ANTHROPIC_API_KEY` | no | Turns on Claude for plans, documents and the assistant. Stays on the server. |
| `ANTHROPIC_MODEL` | no | Claude model ID (default `claude-sonnet-5-5`). |
| `HTTPS_ONLY` | production | `1` = cookies only over HTTPS. |
| `PUBLIC_URL` | no | Public address used in share links. |
| `MISSIONPILOT_DB` | no | Database file path (default `data/missionpilot.db`). |

## Check that it works

- `https://<your-site>/healthz` returns `{"ok": true, ...}`.
- Settings page → **System** shows “Chromium” as the PDF engine and “Claude” if you added a key.
- Optional full test: `python scripts/smoke_test.py https://<your-site> mr out` (creates a test account and checks every page, document and download).

## Backups

The whole database is one file: `/app/data/missionpilot.db`. Copy it regularly (Render: **Shell** tab → `cp /app/data/missionpilot.db /app/data/backup-$(date +%F).db`, or download it).

---

## मराठी मार्गदर्शक

### पर्याय A — Render वर live करणे (सुचवलेले, साधारण 15 मिनिटे)

**1. Code GitHub वर टाका**

- github.com वर लॉग इन करा → **New repository** → नाव `missionpilot-ai` → **Create**.
- “uploading an existing file” वर क्लिक करा आणि `missionpilot` फोल्डरच्या **आतल्या** सगळ्या files आणि folders ओढून टाका (फोल्डर स्वतः नाही, त्याच्या आतले सगळं) → **Commit**.
- किंवा PowerShell मध्ये वरच्या English भागातल्या `git` commands चालवा.

**2. Render वर service तयार करा**

1. https://render.com वर **GitHub ने** sign in करा.
2. **New → Blueprint** → `missionpilot-ai` repository निवडा. Render आपोआप `render.yaml` वाचून service, `SECRET_KEY` आणि data साठी disk तयार करेल.
3. `ANTHROPIC_API_KEY` विचारल्यास Claude ची API key टाका (AI लेखनासाठी). नसल्यास रिकामे ठेवा, तरीही सगळं templates वरून चालेल.
4. **Apply** दाबा. पहिल्या build ला 5–8 मिनिटे लागतात.
5. Render ने दिलेला `https://missionpilot-ai-xxxx.onrender.com` पत्ता उघडा. झाले!

**3. Live झाल्यावर**

- **Environment** मध्ये `PUBLIC_URL` = तुमच्या site चा पत्ता टाका (share links साठी).
- स्वतःचे domain: **Settings → Custom Domains**.
- Code बदलला की GitHub वर push करा. Render आपोआप नवीन version लावतो.

**खर्च आणि data:** Projects कायम राहण्यासाठी disk लागते आणि disk साठी paid instance लागतो (`starter`). Free instance वापरायचा असेल तर `render.yaml` मधला `disk:` भाग काढा. Site चालेल, पण restart झाल्यावर data पुसला जाईल (demo साठी ठीक आहे). किंमत render.com वर तपासा.

### पर्याय B — स्वतःच्या laptop वर (Windows)

1. python.org वरून Python 3.11+ install करा. Install करताना **“Add python.exe to PATH”** tick करा.
2. `run_local.bat` वर double-click करा. पहिल्यांदा packages आणि Chromium install होतात, त्याला काही मिनिटे लागतात.
3. Browser मध्ये http://localhost:8000 उघडा.
4. त्याच Wi-Fi/hotspot वरचे इतर लोक `http://<तुमचा-IP>:8000` उघडू शकतात. IP `ipconfig` ने मिळतो. Windows Firewall ने विचारल्यास Private networks ला परवानगी द्या.

### तपासणी

- `/healthz` उघडल्यावर `{"ok": true}` दिसले पाहिजे.
- Settings → System मध्ये PDF engine “Chromium” दिसले पाहिजे.
