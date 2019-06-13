FROM debian:buster
RUN apt-get update \
  && env DEBIAN_FRONTEND=noninteractive apt-get install -y \
      git \
      gir1.2-glib-2.0 \
      gir1.2-json-1.0 \
      python3-apt \
      python3-gi \
      python3-github \
      python3-ruamel.yaml \
  && apt-get clean
ARG GIT_AUTHOR_NAME="Endless External Data Checker"
# FIXME: emdev@ is not a public mailing list.
# We need something similar to the kernel team's linux@ alias.
ARG GIT_AUTHOR_EMAIL="emdev@endlessm.com"
RUN git config --global user.name "${GIT_AUTHOR_NAME}" \
  && git config --global user.email "${GIT_AUTHOR_EMAIL}"
COPY ./src /app
ENV PATH /app:$PATH
CMD flatpak-external-data-checker
