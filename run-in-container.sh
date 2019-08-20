#!/bin/bash
set -e

GIT_USER_NAME=$(git config user.name)
GIT_USER_EMAIL=$(git config user.email)

user_id_real=$(id -ru 2>/dev/null)
max_uid_count=65536
max_minus_uid=$((max_uid_count - user_id_real))
uid_plus_one=$((user_id_real + 1))

CWD=$(pwd)
HERE=$( dirname "${BASH_SOURCE[0]}" )

podman build -t flatpak-external-data-checker "$HERE"
podman run --rm --privileged \
    -v $HOME:$HOME:rslave \
    -v $CWD:$CWD:rslave \
    -w $CWD \
    -u $(id -u) \
    --uidmap "$user_id_real":0:1 \
    --uidmap 0:1:"$user_id_real" \
    --uidmap "$uid_plus_one":"$uid_plus_one":"$max_minus_uid" \
    -e GIT_AUTHOR_NAME="$GIT_USER_NAME" \
    -e GIT_COMMITTER_NAME="$GIT_USER_NAME" \
    -e GIT_AUTHOR_EMAIL="$GIT_USER_EMAIL" \
    -e GIT_COMMITTER_EMAIL="$GIT_USER_EMAIL" \
    -it flatpak-external-data-checker \
    $*
