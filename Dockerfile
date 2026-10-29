# Sonar in a container.
#   Build: docker build -t sonar .
#   Run:   docker run --rm --shm-size=1g --env-file .env -v ./config:/app/config:ro -v ./data:/app/data -v sonar-hermes-home:/opt/hermes/home sonar [--site NAME] [--days N] [--max-new N]
#   (never publish the browser's debug port; the OpenRouter key comes from .env)
#
# Three Pythons: 3.11 runs Sonar; 3.12 runs the browser-use CLI (a separate tool, pinned, never a project dependency);
# Hermes Agent brings its own (3.14) inside its own folder. Chromium comes from Playwright.
# config/ and data/ are mounted at run time; .env is passed at run time.
#
# Hermes (navigator: hermes in config/settings.yaml) is installed by its own installer, pinned to the commit the developer
# tested (v0.22.4). Its folder paths in config/hermes.yaml are the laptop's Windows paths, so the SONAR_HERMES_* variables below
# replace them inside the container (see load_hermes_settings). Hermes's home (/opt/hermes/home) holds its saved notes
# (skills): mount a NAMED volume there (-v sonar-hermes-home:/opt/hermes/home). Docker fills an empty named volume from the
# image the first time, and the notes then survive new containers. Without the volume, notes are lost when the container is removed.
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

# Not root. data/ is where the database, browser profiles, reports and screenshots live: mount a volume there.
RUN useradd --create-home --uid 1000 sonar && mkdir -p /app/config /app/data /opt/hermes && chown sonar:sonar /app/data /opt/hermes

# Hermes Agent, as the sonar user so nothing needs a (size-doubling) chown afterwards. git/curl are for the installer;
# procps gives pkill/pgrep, which hermes_runner.stop_hermes uses; libatomic1 is needed by the Node.js Hermes downloads. The installer script is fetched at the same pinned
# commit it installs. Our UV_* variables are unset for it: Hermes runs later without them (child_env), so it must use uv's
# defaults at install time too. --skip-browser: Hermes drives OUR Chromium over CDP and uses the browser-use CLI above.
ARG HERMES_COMMIT=4d04303818b61bf3aa09278af48356315515965b
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates procps xz-utils libatomic1 && rm -rf /var/lib/apt/lists/*
USER sonar
RUN curl -fsSL "https://raw.githubusercontent.com/NousResearch/hermes-agent/${HERMES_COMMIT}/scripts/install.sh" -o /tmp/hermes-install.sh \
    && env -u UV_PYTHON_INSTALL_DIR -u UV_TOOL_DIR -u UV_TOOL_BIN_DIR -u UV_COMPILE_BYTECODE -u UV_LINK_MODE \
        bash /tmp/hermes-install.sh --commit "${HERMES_COMMIT}" --dir /opt/hermes/hermes-agent --hermes-home /opt/hermes/home \
        --non-interactive --skip-computer-use --skip-browser \
    && rm /tmp/hermes-install.sh
USER root

COPY src ./src
COPY hermes_plugins ./hermes_plugins
RUN uv sync --frozen --no-dev --python /usr/local/bin/python3.11
USER sonar

ENV PATH="/app/.venv/bin:$PATH" \
    SONAR_HEADLESS=true \
    SONAR_HERMES_HOME=/opt/hermes/home \
    SONAR_HERMES_INSTALL_DIR=/opt/hermes/hermes-agent \
    SONAR_HERMES_EXECUTABLE=/opt/hermes/hermes-agent/.hermes/bin/hermes
ENTRYPOINT ["python", "-m", "sonar.main"]
