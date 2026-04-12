# Ben AI Bridge Engine
# Python 3.12 + TensorFlow + .NET 10 runtime
# Layer order optimized for cache hits: system → pip → models (rare) → code (frequent)
FROM docker.io/ubuntu:24.04

# Install system deps: Python 3.12, libdds, build tools
RUN apt-get update && \
    apt-get -y install --no-install-recommends \
      python3.12 python3.12-venv python3-pip python3.12-dev \
      gcc g++ build-essential \
      libboost-thread-dev libdds-dev \
      libicu-dev \
      curl ca-certificates && \
    update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1 && \
    update-alternatives --install /usr/bin/python python /usr/bin/python3.12 1 && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# .NET 10 runtime for EPBot (CoreCLR) — libicu is required for .NET globalization
RUN curl -sSL https://dot.net/v1/dotnet-install.sh | bash /dev/stdin \
      --channel 10.0 --runtime dotnet --install-dir /usr/share/dotnet && \
    ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet
ENV DOTNET_ROOT=/usr/share/dotnet
ENV PYTHONNET_RUNTIME=coreclr

WORKDIR /app

# Install Python dependencies — cached unless requirements.txt changes
COPY requirements.txt .
RUN python3.12 -m pip install --break-system-packages --no-cache-dir -r requirements.txt && \
    apt-get purge -y --auto-remove gcc g++ build-essential python3.12-dev && \
    rm -rf /var/lib/apt/lists/*

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
ADD src/bidding /app/bidding/
ADD src/nn /app/nn/
ADD src/ddsolver /app/ddsolver/
ADD src/alphamju /app/alphamju/
ADD src/ace /app/ace/
ADD src/bba /app/bba/
ADD src/pimc /app/pimc/
ADD src/suitc /app/suitc/
ADD src/openinglead /app/openinglead/
ADD bin /app/bin/
ADD src/config /app/config/
ADD models /app/models/
COPY "BBA/CC/" "/app/BBA/CC/"
ADD start_ben_all.sh /app/

# BBA imports from "src.objects" — create symlink so /app/src -> /app
# Grant execution permissions to the script and fix line endings
RUN ln -s /app /app/src && \
    ln -s /app /src && \
    ln -s /app/BBA /BBA && \
    sed -i 's/\r$//' /app/start_ben_all.sh && chmod +x /app/start_ben_all.sh

# PIMC native binaries were compiled against boost 1.74; Ubuntu 24.04 ships 1.83.
# Create compatibility symlinks so dlopen finds the expected sonames.
RUN ln -sf /usr/lib/x86_64-linux-gnu/libboost_thread.so.1.83.0 /usr/lib/x86_64-linux-gnu/libboost_thread.so.1.74.0 && \
    ln -sf /usr/lib/x86_64-linux-gnu/libboost_system.so.1.83.0 /usr/lib/x86_64-linux-gnu/libboost_system.so.1.74.0 && \
    ln -sf /usr/lib/x86_64-linux-gnu/libboost_date_time.so.1.83.0 /usr/lib/x86_64-linux-gnu/libboost_date_time.so.1.74.0 && \
    ln -sf /usr/lib/x86_64-linux-gnu/libboost_chrono.so.1.83.0 /usr/lib/x86_64-linux-gnu/libboost_chrono.so.1.74.0

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8085/ || exit 1

CMD ["gunicorn", "--config", "gunicorn.conf.py", "gameapi:app"]
