#!/bin/sh
# Rebuild and (re)start a local test container against ./testdata
cd "$(dirname "$0")"
docker build -q -t mediaman:dev . >/dev/null || exit 1
docker rm -f mediaman-test >/dev/null 2>&1
docker run -d --name mediaman-test -p 8765:8765 \
  -v "$PWD/${TESTDATA:-testdata}/config":/config -v "$PWD/${TESTDATA:-testdata}/media":/media -v "$PWD/${TESTDATA:-testdata}/downloads":/downloads mediaman:dev
