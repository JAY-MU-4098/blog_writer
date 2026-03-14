# Blog Writer (simple dev layout)

AI-powered blog writer: same pipeline as the full app, in fewer files. Generate SEO-optimized posts from a topic; optional web research (Tavily) and email delivery (Resend).

**Files:**
- **main.py** – FastAPI app (run with uvicorn for local API)
- **nodes.py** – state, config, LLM, all graph nodes
- **graph.py** – LangGraph build (edges + conditionals)
- **run.py** – pipeline functions + CLI
- **blog_generator.py** – example script calling the pipeline
- **.env** – API keys (copy from `.env.example` if available)

See **PROJECT_EXPLANATION.txt** for a full description of what the project does and how it works.

---

## Setup

```bash
cd blog_writer_simple
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

Create a `.env` file with your keys (or copy from `.env.example` if present):

```
OPENAI_API_KEY=sk-...
# Optional:
TAVILY_API_KEY=...
RESEND_API_KEY=...
RESEND_FROM_EMAIL=onboarding@resend.dev
OPENAI_MODEL=gpt-4.1-mini
BLOG_QUALITY_THRESHOLD=7
```

---

## Run

### 1. As API (FastAPI, local)

Start the API server:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

- **Docs (Swagger):** http://localhost:8000/docs  
- **Health:** http://localhost:8000/health  

Generate a blog with `POST /generate` and a JSON body:

```json
{
  "topic": "Best running shoes",
  "approx_max_words": 1200,
  "recipient_email": ["you@example.com"],
  "goal": "traffic",
  "tone": "helpful, direct",
  "location": null,
  "business_context": null
}
```

### 2. As CLI

```bash
python run.py --topic "Best running shoes" --approx-max-words 1200 --recipient-email you@example.com --goal traffic --tone "helpful, direct"
```

Optional: `--location`, `--business-context`.

### 3. From code

```python
from run import run_pipeline, run_pipeline_async

result = run_pipeline(
    topic="Best running shoes",
    approx_max_words=1200,
    recipient_email="you@example.com",
    goal="traffic",
)
print(result["final_blog"])
print(result["email"])
```

### 4. With Docker (local build)

```bash
docker build -t blog-writer .
docker run --env-file .env -p 8000:8000 blog-writer
```

API is at http://localhost:8000 (docs at http://localhost:8000/docs). Pass env vars via `-e OPENAI_API_KEY=...` or `--env-file .env`.

---

## Publish image for other users (Docker Hub)

To make the image public so others can pull and run it:

1. **Create a Docker Hub account** at https://hub.docker.com and sign in:
   ```bash
   docker login
   ```

2. **Tag the image** with your Docker Hub username and repo name (e.g. `yourusername/blog-writer-simple`):
   ```bash
   docker build -t yourusername/blog-writer-simple:latest .
   docker tag yourusername/blog-writer-simple:latest yourusername/blog-writer-simple:1.0
   ```

3. **Push to Docker Hub**:
   ```bash
   docker push yourusername/blog-writer-simple:latest
   docker push yourusername/blog-writer-simple:1.0
   ```

4. On Docker Hub, set the repository to **Public** (Settings → Make public). Then anyone can pull and run the image (see below).

---

## For other users: run the public image

If the image is published on Docker Hub (e.g. `yourusername/blog-writer-simple`), anyone can run it without building:

**Using Docker:**

```bash
# Pull the image (one time)
docker pull yourusername/blog-writer-simple:latest

# Run with your own API key (required)
docker run -p 8000:8000 \
  -e OPENAI_API_KEY=sk-your-key-here \
  yourusername/blog-writer-simple:latest
```

**Using a .env file:** create a file (e.g. `my.env`) with `OPENAI_API_KEY=...` and optional keys, then:

```bash
docker run -p 8000:8000 --env-file my.env yourusername/blog-writer-simple:latest
```

**Using docker-compose:** copy `.env.example` to `.env`, fill in `OPENAI_API_KEY`, then use the compose file that references the public image (see `docker-compose.public.yml` in the repo).

- API: http://localhost:8000  
- Interactive docs: http://localhost:8000/docs  
- Health: http://localhost:8000/health  

Users must provide at least `OPENAI_API_KEY`; optional: `TAVILY_API_KEY`, `RESEND_API_KEY`, etc. (see `.env.example`).
