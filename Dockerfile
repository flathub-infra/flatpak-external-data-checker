FROM debian:buster as unappimage
RUN apt-get update && \
    env DEBIAN_FRONTEND=noninteractive apt-get install -y make gcc git libelf-dev && \
    git clone https://github.com/refi64/unappimage && \
    cd unappimage && make -C squashfs-tools -j$(nproc)

FROM debian:buster
COPY --from=unappimage /unappimage/squashfs-tools/unappimage /usr/local/bin/
RUN apt-get update \
  && env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      bubblewrap \
      git \
      gir1.2-glib-2.0 \
      gir1.2-json-1.0 \
      python3-apt \
      python3-gi \
      python3-github \
      python3-ruamel.yaml \
      python3-tenacity \
  && apt-get clean \
  && rmdir /var/cache/apt/archives/partial

ARG USER_ID=1000
ARG GROUP_ID=1000
RUN groupadd -g $GROUP_ID user && \
    useradd -u $USER_ID -s /bin/sh -m -g user user

CMD /bin/sh
