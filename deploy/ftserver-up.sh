#!/usr/bin/env bash
# Brings the FreeTAKServer compose stack up, working around the
# docker-compose v1 "KeyError: 'ContainerConfig'" crash.
#
# That bug fires when compose tries to *recreate* an existing container
# (which it does whenever compose.yaml has changed) against images built
# with buildkit. It leaves the old containers renamed and stopped, so the
# stack ends up down. Retrying after an explicit rm -f creates them fresh
# and succeeds.
set -uo pipefail

COMPOSE=/usr/bin/docker-compose
FILE=compose.yaml
cd "$(dirname "${BASH_SOURCE[0]}")/../ftserver"

if $COMPOSE -f "$FILE" up -d; then
  exit 0
fi

echo "compose up failed; retrying after removing containers (v1 recreate bug)" >&2
$COMPOSE -f "$FILE" stop || true
$COMPOSE -f "$FILE" rm -f || true
exec $COMPOSE -f "$FILE" up -d
