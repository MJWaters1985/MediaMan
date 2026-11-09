# MediaMan — Jellyfin/qBittorrent media manager with hardware transcoding.
# jellyfin-ffmpeg ships the Intel media driver (QSV/VAAPI), Mesa (AMD VAAPI) and NVENC support.
FROM python:3.12-slim-trixie

ARG JELLYFIN_FFMPEG=jellyfin-ffmpeg7
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl gnupg tini \
 && mkdir -p /etc/apt/keyrings \
 && curl -fsSL https://repo.jellyfin.org/jellyfin_team.gpg.key | gpg --dearmor -o /etc/apt/keyrings/jellyfin.gpg \
 && echo "deb [signed-by=/etc/apt/keyrings/jellyfin.gpg] https://repo.jellyfin.org/debian trixie main" > /etc/apt/sources.list.d/jellyfin.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends ${JELLYFIN_FFMPEG} mkvtoolnix \
 && apt-get purge -y gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Netflix vmaf CLI (per-title quality targeting), quietvoid hdr10plus_tool (HDR10+ metadata) and
# dovi_tool (Dolby Vision profile 7 → 8.1 RPU conversion),
# pinned to release versions and verified against their published SHA-256 digests.
ARG VMAF_VERSION=v3.2.1
ARG HDR10PLUS_VERSION=1.7.2
ARG DOVI_VERSION=2.3.4
RUN set -e; arch="$(uname -m)"; \
    case "$arch" in \
      x86_64)  vz=vmaf-linux-x86_64-gcc.zip; vs=c0bb6d354f7009530fab5749e1309bd90f9c45a5a10e90250e6e558c48cfdd02; \
               ht=hdr10plus_tool-${HDR10PLUS_VERSION}-x86_64-unknown-linux-musl.tar.gz; hs=06385f37a639d61ba21d4be3150c863846933bc3b58110e094d8fc8f1c2249f2; \
               dt=dovi_tool-${DOVI_VERSION}-x86_64-unknown-linux-musl.tar.gz; ds=1844258e13c26607b32224bf1fa82b595d3b35949f5467405fda560daad32b3f ;; \
      aarch64) vz=vmaf-linux-arm64-clang.zip; vs=94fddd3fa6440c68664d0498eb0716ae0808e2bae2ecede767deb6a30753c74e; \
               ht=hdr10plus_tool-${HDR10PLUS_VERSION}-aarch64-unknown-linux-musl.tar.gz; hs=5fb90607cd94296640f1fc2355207b8107b67baac96d37481423d08a9fce437d; \
               dt=dovi_tool-${DOVI_VERSION}-aarch64-unknown-linux-musl.tar.gz; ds=b4f22a7db56954efe4ed8d02276d0f991799e84602f3584711be6c9df950cb15 ;; \
      *) echo "unsupported arch $arch"; exit 1 ;; \
    esac; \
    mkdir -p /tmp/t && cd /tmp/t; \
    curl -fsSLo vmaf.zip "https://github.com/Netflix/vmaf/releases/download/${VMAF_VERSION}/$vz"; \
    echo "$vs  vmaf.zip" | sha256sum -c -; \
    curl -fsSLo h.tgz "https://github.com/quietvoid/hdr10plus_tool/releases/download/${HDR10PLUS_VERSION}/$ht"; \
    echo "$hs  h.tgz" | sha256sum -c -; \
    curl -fsSLo d.tgz "https://github.com/quietvoid/dovi_tool/releases/download/${DOVI_VERSION}/$dt"; \
    echo "$ds  d.tgz" | sha256sum -c -; \
    python3 -c "import zipfile; zipfile.ZipFile('vmaf.zip').extractall('v')"; \
    tar -xzf h.tgz; tar -xzf d.tgz; \
    install -m 0755 "$(find v -type f -name vmaf | head -1)" /usr/local/bin/vmaf; \
    install -m 0755 "$(find . -type f -name hdr10plus_tool | head -1)" /usr/local/bin/hdr10plus_tool; \
    install -m 0755 "$(find . -type f -name dovi_tool | head -1)" /usr/local/bin/dovi_tool; \
    cd / && rm -rf /tmp/t; vmaf --version; hdr10plus_tool --version; dovi_tool --version

ENV MEDIAMAN_CONFIG=/config \
    PYTHONUNBUFFERED=1 \
    PATH="/usr/lib/jellyfin-ffmpeg:${PATH}"

WORKDIR /opt/mediaman
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app

EXPOSE 8765
# start-interval: check every 3s right after start so TrueNAS leaves "Deploying" quickly
HEALTHCHECK --interval=60s --timeout=5s --start-period=60s --start-interval=3s CMD ["python", "-m", "app.healthcheck"]
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "app.server"]
