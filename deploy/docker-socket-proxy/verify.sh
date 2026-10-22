#!/bin/sh
# Verifies the allowlist in haproxy.cfg.template against the real proxy image
# (bead DocuFlux-6b5). Uses only requests that create nothing: start/stop on a
# nonexistent id returns 404 from dockerd, so "not 403" means "forwarded".
# Usage: deploy/docker-socket-proxy/verify.sh
set -eu
cd "$(dirname "$0")"
IMAGE=tecnativa/docker-socket-proxy:0.3.0
NAME=dsp-verify-$$
docker run -d --rm --name "$NAME" \
  -v /var/run/docker.sock:/var/run/docker.sock:ro \
  -v "$PWD/haproxy.cfg.template:/usr/local/etc/haproxy/haproxy.cfg.template:ro" \
  "$IMAGE" >/dev/null
trap 'docker stop "$NAME" >/dev/null 2>&1 || true' EXIT
sleep 2
fail=0
req() { # method path -> http status
  if [ "$1" = GET ]; then
    docker run --rm --network "container:$NAME" curlimages/curl:8.10.1 -s -o /dev/null -w '%{http_code}' "http://localhost:2375$2"
  else
    docker run --rm --network "container:$NAME" curlimages/curl:8.10.1 -s -o /dev/null -w '%{http_code}' -X "$1" "http://localhost:2375$2"
  fi
}
expect() { # method path allowed|denied
  s=$(req "$1" "$2")
  if [ "$3" = denied ]; then ok=$([ "$s" = 403 ] && echo y || echo n)
  else ok=$([ "$s" != 403 ] && [ "$s" != 000 ] && echo y || echo n); fi
  printf '%-4s %-5s %-45s %s\n' "$([ $ok = y ] && echo PASS || echo FAIL)" "$1" "$2" "-> $s ($3)"
  [ $ok = y ] || fail=1
}
ID=0000000000000000000000000000000000000000000000000000000000000000
expect GET  /_ping allowed
expect GET  '/containers/json?all=1' allowed
expect GET  /v1.43/containers/json allowed
expect POST /containers/$ID/start allowed
expect POST /v1.43/containers/$ID/stop allowed
expect POST /containers/create denied
expect POST /containers/$ID/exec denied
expect POST /containers/$ID/kill denied
expect POST /containers/$ID/restart denied
expect POST /containers/$ID/update denied
expect POST /containers/$ID/start/x denied
expect GET  /containers/$ID/json denied
expect DELETE /containers/$ID denied
expect POST /exec/$ID/start denied
expect POST /images/create denied
expect GET  /info denied
exit $fail
