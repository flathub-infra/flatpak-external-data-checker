FROM debian:bullseye

ENV DEBIAN_FRONTEND=noninteractive

ADD dependencies.apt.txt ./

RUN apt-get update && \
    xargs apt-get install --no-install-recommends -y < dependencies.apt.txt && \
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
