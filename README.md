# Blog Writer

AI-powered blog writer: generate SEO-optimized posts from a topic. Optional web research (Tavily) and email delivery (Resend).

**GitHub:** [https://github.com/JAY-MU-4098/blog_writer](https://github.com/JAY-MU-4098/blog_writer)

---

## Run with Docker (on your PC)

Use the public Docker image to run the API locally. You need an [OpenAI API key](https://platform.openai.com/api-keys).

### 1. Pull the image

```bash
docker pull jaygogra/blog-writer-simple:latest
```

### 2. Run the container

**Option A – pass your API key directly:**

```bash
docker run -p 8000:8000 -e OPENAI_API_KEY=sk-your-key-here jaygogra/blog-writer-simple:latest
```

**Option B – use a `.env` file:**  
Create a file (e.g. `my.env`) with:

```
OPENAI_API_KEY=sk-your-key-here
```

Then run:

```bash
docker run -p 8000:8000 --env-file my.env jaygogra/blog-writer-simple:latest
```

**Option C – Docker Compose (recommended):**  
From the folder where you have `docker-compose.yml` (or `docker-compose.public.yml`), run:

```bash
docker compose up -d
```

Generated blog HTML files are saved in a **`generated_blog`** folder **in that same folder** (on your PC). The compose file mounts `./generated_blog` so files appear where you ran the command (e.g. your desktop), not inside the container.

**Option D – plain `docker run` but save files on your PC:**  
Mount a folder so generated blogs appear on your machine:

```bash
docker run -p 8000:8000 -v $(pwd)/generated_blog:/app/generated_blog -e OPENAI_API_KEY=sk-your-key jaygogra/blog-writer-simple:latest
```

### 3. Use the API

- **API:** http://localhost:8000  
- **Interactive docs (Swagger):** http://localhost:8000/docs  
- **Health check:** http://localhost:8000/health  

Generate a blog with **POST** `/generate` and a JSON body, for example:

```json
{
  "topic": "Best running shoes",
  "approx_max_words": 1200,
  "recipient_email": ["you@example.com"],
  "goal": "traffic",
  "tone": "helpful, direct"
}
```

Optional env vars (e.g. in `my.env`): `TAVILY_API_KEY`, `RESEND_API_KEY`. See `.env.example` in the [repo](https://github.com/JAY-MU-4098/blog_writer) for all options.
