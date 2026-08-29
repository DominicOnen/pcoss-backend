# PCOSS Backend (FastAPI)

API backing the PCOSS church website (deployed separately on Cloudflare Pages,
since Cloudflare Pages can't run PHP or Python).

## Endpoints

| Method | Path            | Auth        | Purpose                                   |
|--------|-----------------|-------------|--------------------------------------------|
| GET    | `/api/sermons`  | public      | List sermons                              |
| POST   | `/api/sermons`  | admin key   | Add a sermon                              |
| GET    | `/api/verses`   | public      | List daily verses                         |
| POST   | `/api/verses`   | admin key   | Add a verse                               |
| GET    | `/api/updates`  | public      | `{ events: [...], announcements: [...] }` |
| POST   | `/api/updates`  | admin key   | Add an event or announcement              |
| GET    | `/api/gallery`  | public      | List gallery items                        |
| POST   | `/api/gallery`  | admin key   | Upload a gallery image/video (multipart)  |
| POST   | `/api/contact`  | public      | Submit the contact form                   |

Admin-only routes require an `X-Admin-Key` header matching the `ADMIN_KEY`
environment variable.

## Local development

```bash
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then fill in real values
uvicorn main:app --reload
```

Without a `DATABASE_URL` set, the app falls back to a local SQLite file
(`pcoss_dev.db`) so you can develop without touching production data.

## Deploying (Render)

1. Push this repo to GitHub.
2. In Render: New -> Web Service -> connect this repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add every variable from `.env.example` under Render's Environment tab
   (with real values — never commit real secrets to this repo).
6. Once deployed, confirm your frontend's `config.js` (`API_BASE_URL`)
   points at this service's Render URL.

## Security note

An earlier version of `main.py` had a live database password hardcoded as a
fallback value. It has been removed and the password should be **rotated**
in Supabase if it hasn't been already — assume anything ever committed to
git history is compromised.
