# Sonar in a container.
#   Build: docker build -t sonar .
#   Run:   docker run --rm --shm-size=1g --env-file .env -v ./config:/app/config:ro -v ./data:/app/data sonar [--site NAME] [--days N] [--max-new N]
#   (never publish the browser's debug port; the OpenRouter key comes from .env)
#
# Two Pythons: 3.11 runs Sonar; 3.12 runs the browser-use CLI (a separate tool, pinned, never a project dependency).
# Chromium comes from Playwright. config/ and data/ are mounted at run time; .env is passed at run time.
FROM python:3.11-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /usr/local/bin/

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_INSTALL_DIR=/opt/uv-python \
    UV_TOOL_DIR=/opt/uv-tools \
    UV_TOOL_BIN_DIR=/usr/local/bin \
    PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8

# The browser-use CLI on its own Python 3.12 (uv downloads that Python into /opt/uv-python).
RUN uv tool install browser-use==0.13.11 --python 3.12 && chmod -R a+rX /opt/uv-python /opt/uv-tools

WORKDIR /app

# Dependencies first (cached unless the lock file changes), then Chromium and the system libraries
# it needs (big, so before our code: a code change must not download it again), then our code.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python3.11
RUN /app/.venv/bin/playwright install --with-deps chromium && chmod -R a+rX /opt/ms-playwright
COPY src ./src
RUN uv sync --frozen --no-dev --python /usr/local/bin/python3.11

# Not root. data/ is where the database, browser profiles, reports and screenshots live: mount a volume there.
RUN useradd --create-home --uid 1000 sonar && mkdir -p /app/config /app/data && chown sonar:sonar /app/data
USER sonar

ENV PATH="/app/.venv/bin:$PATH" \
    SONAR_HEADLESS=true
ENTRYPOINT ["python", "-m", "sonar.main"]
