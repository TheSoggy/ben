# Ben AI Bridge Engine
# Python 3.12 + TensorFlow + .NET 10 runtime
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

# .NET 10 runtime for PIMC, ACE, and BBA components
RUN curl -sSL https://dot.net/v1/dotnet-install.sh | bash /dev/stdin \
      --channel 10.0 --runtime dotnet --install-dir /usr/share/dotnet && \
    ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet
ENV DOTNET_ROOT=/usr/share/dotnet
ENV PYTHONNET_RUNTIME=coreclr

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN python3.12 -m pip install --break-system-packages --no-cache-dir -r requirements.txt

# Suppress TensorFlow/CUDA warnings (no GPU in container)
ENV TF_CPP_MIN_LOG_LEVEL=2
ENV CUDA_VISIBLE_DEVICES=""

# Copy application code
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
COPY bin /app/bin/
COPY src/config /app/config/
COPY models /app/models/
COPY BBA/CC/ /BBA/CC/

# BBA imports "from src.objects" expecting repo-root/src/ layout
# Create /src symlink so the import resolves to /app/objects.py
RUN ln -s /app /src

EXPOSE 8085

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD curl -f http://localhost:8085/health || exit 1

CMD ["python3.12", "gameapi.py", "--port", "8085", "--config", "config/bridgearena_api.conf"]
