FROM debian:buster

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install -y \
      bubblewrap \
      git \
      gir1.2-glib-2.0 \
      gir1.2-json-1.0 \
      python3-apt \
      python3-defusedxml \
      python3-gi \
      python3-github \
      python3-pip \
      python3-ruamel.yaml \
      python3-setuptools \
      python3-tenacity \
      python3-toml \
      python3-pyelftools \
      squashfs-tools \
      jq \
  && apt-get clean \
  && rmdir /var/cache/apt/archives/partial

COPY src /app/src
COPY flatpak-external-data-checker /app/
COPY canonicalize-manifest /app/

RUN python3 -m compileall /app/src

ENTRYPOINT [ "/app/flatpak-external-data-checker" ]
