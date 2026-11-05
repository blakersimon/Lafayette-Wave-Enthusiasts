# Railway & Porkbun Custom Domain Deployment Guide (`Louisiana Surf Finder`)

This project is pre-configured for one-command deployment on **Railway** with custom domain DNS + Let's Encrypt SSL managed on **Porkbun**, following the `blake-skills-railway-porkbun-deployment` workflow.

---

## 1. Railway Build & Runtime Files

- [`Procfile`](../Procfile):
  ```
  web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2} --proxy-headers --forwarded-allow-ips="*"
  ```
- [`railway.toml`](../railway.toml):
  - Builder: `NIXPACKS`
  - Healthcheck Path: `/api/health` (`30s` timeout)
  - Restart Policy: `ON_FAILURE` (up to 5 retries)

---

## 2. Step-by-Step Deployment

### Step 1: Push to GitHub & Link Railway Service
```bash
git init
git add .
git commit -m "Initial release: Louisiana Surf Finder consensus engine & map dashboard"
gh repo create louisiana-surf-finder --private --source=. --remote=origin --push

railway init --name louisiana-surf-finder
railway up --detach
```

### Step 2: Automated Railway + Porkbun DNS Wiring
Once the service is running on Railway, set your Railway and Porkbun API credentials and run [`scripts/deploy_railway_porkbun.py`](../scripts/deploy_railway_porkbun.py) (or [`scripts/setup_porkbun_dns.py`](../scripts/setup_porkbun_dns.py)):

```bash
export RAILWAY_API_TOKEN="your_railway_token"
export PORKBUN_API_KEY="pk1_your_porkbun_key"
export PORKBUN_SECRET_KEY="sk1_your_porkbun_secret"

python3 scripts/deploy_railway_porkbun.py \
  --domain louisianasurffinder.com \
  --project-id <PROJECT_ID> \
  --environment-id <ENVIRONMENT_ID> \
  --service-id <SERVICE_ID>
```

Or if you already have the Railway `.up.railway.app` domain target:
```bash
python3 scripts/setup_porkbun_dns.py \
  --domain louisianasurffinder.com \
  --target louisiana-surf-finder-production.up.railway.app
```

---

## 3. Health & Telemetry Verification
Verify deployment readiness via `/api/health`:
```bash
curl -s https://louisianasurffinder.com/api/health | jq .
```
