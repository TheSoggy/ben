# Ben AI Bridge Engine
# Multi-stage build: compile native extensions in builder, slim final image
# Layer order optimized for cache hits: system → models (rare) → code (frequent)

# =============================================================================
# Stage 1: Builder — install system deps + compile Python packages
# =============================================================================
FROM docker.io/ubuntu:24.04 AS builder

RUN apt-get update && \
    apt-get -y install --no-install-recommends \
      python3.12 python3.12-venv python3.12-dev python3-pip \
      gcc g++ build-essential \
      libboost-thread-dev libdds-dev \
      libicu-dev \
      curl ca-certificates && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# .NET 10 runtime for PIMC, ACE, and BBA components
RUN curl -sSL https://dot.net/v1/dotnet-install.sh | bash /dev/stdin \
      --channel 10.0 --runtime dotnet --install-dir /usr/share/dotnet && \
    ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet

WORKDIR /app

# Install Python deps — cached unless requirements.txt changes
COPY requirements.txt .
RUN python3.12 -m pip install --break-system-packages --no-cache-dir -r requirements.txt

# =============================================================================
# Stage 2: Runtime — slim image without build tools
# =============================================================================
FROM docker.io/ubuntu:24.04

# Runtime-only system packages (no gcc/g++/build-essential)
RUN apt-get update && \
    apt-get -y install --no-install-recommends \
      python3.12 python3.12-venv \
      libboost-thread1.83.0 libdds \
      libicu74 \
      curl ca-certificates && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Copy .NET runtime from builder
COPY --from=builder /usr/share/dotnet /usr/share/dotnet
RUN ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet
ENV DOTNET_ROOT=/usr/share/dotnet
ENV PYTHONNET_RUNTIME=coreclr

# Copy installed Python packages from builder
COPY --from=builder /usr/lib/python3/dist-packages /usr/lib/python3/dist-packages
COPY --from=builder /usr/local/lib/python3.12/dist-packages /usr/local/lib/python3.12/dist-packages
COPY --from=builder /usr/local/bin /usr/local/bin

WORKDIR /app

# Suppress TensorFlow/CUDA warnings (no GPU in container)
ENV TF_CPP_MIN_LOG_LEVEL=2
ENV CUDA_VISIBLE_DEVICES=""

# Ben configuration via env vars (used by gunicorn.conf.py and gameapi.py argparse defaults)
ENV BEN_HOST=0.0.0.0
ENV BEN_PORT=8085
ENV BEN_CONFIG=config/bridgearena_api.conf
ENV BEN_WORKERS=16
ENV BEN_NOLIMIT=True

# --- Layer order: least-changing first for best cache hits ---

# 1. Models (~250MB, change rarely) — cached across code changes
COPY models /app/models/
COPY BBA/CC/ /app/BBA/CC/

# 2. Static assets and native libraries (change rarely)
COPY bin /app/bin/

# 3. Application code (changes frequently — only invalidates layers below)
COPY src/config /app/config/
COPY src/frontend /app/frontend/
COPY src/*.py /app/
COPY src/bidding /app/bidding/
COPY src/nn /app/nn/
COPY src/ddsolver /app/ddsolver/
COPY src/alphamju /app/alphamju/
COPY src/ace /app/ace/
COPY src/bba /app/bba/
COPY src/pimc /app/pimc/
COPY src/suitc /app/suitc/
COPY src/openinglead /app/openinglead/

# BBA imports "from src.objects" expecting repo-root/src/ layout
RUN ln -s /app /src

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8085/ || exit 1

CMD ["gunicorn", "--config", "gunicorn.conf.py", "gameapi:app"]
