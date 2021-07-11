FROM debian:bullseye

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && \
    apt-get install --no-install-recommends -y \
      bubblewrap \
      git \
      gir1.2-glib-2.0 \
      gir1.2-json-1.0 \
      python3-apt \
      python3-defusedxml \
      python3-aiohttp \
      python3-chardet \
      python3-aiodns \
      python3-brotli \
      python3-gi \
      python3-cairo \
      python3-requests \
      python3-github \
      python3-pip \
      python3-ruamel.yaml \
      python3-setuptools \
      python3-toml \
      python3-pyelftools \
      python3-semver \
      python3-jsonschema \
      squashfs-tools \
      ssh-client \
      jq \
    && \
  apt-get clean && \
  rmdir /var/cache/apt/archives/partial

ADD requirements.txt ./

RUN python3 -m pip install -r requirements.txt && \
    rm -rf $HOME/.cache/pip

# Creating the user is required because jenkins runs he container
# with the same user as the host (with '-u <uid>:<gid>')
# but without the user existing 'git' fails with 'No user exists for uid ...'.
ARG USER_ID=1000
ARG GROUP_ID=1000
RUN groupadd -g $GROUP_ID user && \
    useradd -u $USER_ID -s /bin/sh -m -g user user

COPY src /app/src
COPY flatpak-external-data-checker /app/
COPY canonicalize-manifest /app/

RUN python3 -m compileall /app/src

ENTRYPOINT [ "/app/flatpak-external-data-checker" ]
