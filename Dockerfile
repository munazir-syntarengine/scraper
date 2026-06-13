# Brand Website Scraper — container image for Railway (or any Docker host).
#
# Uses the official Playwright Python image, which ships headless Chromium AND
# all the OS libraries it needs (libnss, fonts, etc.) preinstalled — the
# reliable base for a browser app. The tag is pinned to the same Playwright
# version as requirements.txt so the bundled browser matches the pip package.
FROM mcr.microsoft.com/playwright/python:v1.60.0-noble

WORKDIR /app

# Install Python deps first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Guarantee the Chromium build matching the installed Playwright is present.
# (The base image bundles browsers + OS deps; this is a belt-and-suspenders
# step against any version drift — it's a no-op when versions already align.)
RUN playwright install chromium

# Application code.
COPY . .

# Railway injects $PORT at runtime; default to 8000 for local `docker run`.
ENV PORT=8000
EXPOSE 8000

# Exec form (so it receives OS signals) + `exec` so uvicorn replaces the shell
# as PID 1 for clean shutdowns; the shell expands ${PORT}. Bind 0.0.0.0.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
