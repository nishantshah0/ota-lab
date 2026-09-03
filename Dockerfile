# Reproducible environment: ARM toolchain, CMake, Renode, pytest.
#
#   docker build -t ota-lab .
#   docker run --rm ota-lab                # runs the pytest suite
#   docker run --rm -it ota-lab bash       # interactive shell
FROM ubuntu:22.04

ARG RENODE_VERSION=1.16.1
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc-arm-none-eabi binutils-arm-none-eabi \
        cmake ninja-build \
        python3 python3-pip \
        wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Renode portable bundle (self contained, no Mono install needed).
RUN wget -q -O /tmp/renode.tar.gz \
        "https://github.com/renode/renode/releases/download/v${RENODE_VERSION}/renode-${RENODE_VERSION}.linux-portable.tar.gz" \
    && mkdir -p /opt/renode \
    && tar -xzf /tmp/renode.tar.gz --strip-components=1 -C /opt/renode \
    && rm /tmp/renode.tar.gz

ENV PATH="/opt/renode:${PATH}" \
    RENODE=/opt/renode/renode

WORKDIR /work

COPY tests/requirements.txt tests/requirements.txt
COPY tools/requirements.txt tools/requirements.txt
RUN pip3 install --no-cache-dir -r tests/requirements.txt

COPY . .

RUN cmake -S . -B build -G Ninja && cmake --build build

CMD ["pytest", "-v"]
