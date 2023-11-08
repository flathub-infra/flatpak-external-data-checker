FROM debian:bookworm

ENV DEBIAN_FRONTEND=noninteractive

ADD dependencies.apt.txt ./

RUN sed -i "s/Types: deb/Types: deb deb-src/" /etc/apt/sources.list.d/debian.sources && \
    apt-get update && \
    xargs apt-get install --no-install-recommends -y < dependencies.apt.txt && \
    apt-get --no-install-recommends -y build-dep python3-apt && \
    apt-get clean && \
    rmdir /var/cache/apt/archives/partial

# All requirements should be satisfied by dependencies.apt.txt – but feed it to
# pip to keep us honest
ADD requirements.txt ./
RUN sed -i 's/python-apt @ .*/python-apt/' requirements.txt && \
    pip install --break-system-packages -r requirements.txt && \
    rm -rf $HOME/.cache/pip

COPY src /app/src
COPY flatpak-external-data-checker /app/
COPY canonicalize-manifest /app/

RUN python3 -m compileall /app/src

ENTRYPOINT [ "/app/flatpak-external-data-checker" ]
