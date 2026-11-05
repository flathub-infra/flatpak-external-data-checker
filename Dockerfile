FROM debian:trixie

ENV DEBIAN_FRONTEND=noninteractive

COPY dependencies.apt.txt /

RUN apt-get update && \
    xargs apt-get install --no-install-recommends -y < dependencies.apt.txt && \
    apt-get clean && \
    rmdir /var/cache/apt/archives/partial && \
    rm -rf /var/lib/apt/lists/*

COPY src /app/src
COPY flatpak-external-data-checker /app/
COPY canonicalize-manifest /app/

RUN python3 -m compileall /app/src

ENTRYPOINT [ "/app/flatpak-external-data-checker" ]
