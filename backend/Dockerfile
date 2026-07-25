# syntax=docker/dockerfile:1
#
# LeadBoost backend -- multi-stage build.
#
# Stage 1 (builder) installs build tooling and Python dependencies into a
# venv. Stage 2 (runtime) copies only that venv plus the app source into a
# slim image with no compilers/headers left behind, keeping the final
# image as small as this stack allows.
#
# Playwright's Chromium is a genuine, unavoidable weight cost here: the
# scraper already depends on it for its higher-fidelity fetch tiers (an
# existing, working part of the architecture -- not something this pass
# rewrites or removes). See docs/DOCKER.md for a note on splitting
# scraping into its own service if memory becomes tight in production.

# ---------------------------------------------------------------------------
# Stage 1: builder
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build-time system packages needed to compile a couple of wheels
# (psycopg2-binary ships prebuilt, but cryptography/curl_cffi sometimes
# need these on less-common platforms) -- none of this ships in the
# final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright

# Runtime system packages: libpq5 (psycopg2-binary needs the client
# library even though the wheel is prebuilt) and the Chromium runtime
# dependencies Playwright needs to actually launch a browser.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv

# Playwright's own installer knows exactly which OS packages its bundled
# Chromium build needs -- letting it manage that (rather than
# hand-maintaining a system-package list here) is what keeps this working
# across Playwright version bumps.
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.cache/pip

# Non-root runtime user.
RUN groupadd --gid 1000 leadboost && \
    useradd --uid 1000 --gid leadboost --shell /bin/bash --create-home leadboost

WORKDIR /app
COPY --chown=leadboost:leadboost . .

# The builder stage's /build isn't copied in, and .dockerignore keeps
# tests/local-only files out of the image entirely -- see .dockerignore.

USER leadboost

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/live || exit 1

# Multiple workers would multiply Playwright/browser memory usage, which
# is the wrong tradeoff on a memory-constrained target (see SECTION 3 of
# the brief) -- one worker, relying on FastAPI's own async concurrency
# for I/O-bound request handling, which is the overwhelming majority of
# this API's workload.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
