#!/usr/bin/env bash
# Fail unless engine, backend and frontend declare the same release version,
# and (on a tag build) the tag is exactly v<version>. See docs/release/.
set -euo pipefail
cd "$(dirname "$0")/.."
engine="$(sed -n 's/^version = "\(.*\)"$/\1/p' engine/pyproject.toml)"
backend="$(sed -n 's/^version = "\(.*\)"$/\1/p' backend/pyproject.toml)"
frontend="$(sed -n 's/^  "version": "\(.*\)",$/\1/p' frontend/package.json)"
echo "engine=${engine} backend=${backend} frontend=${frontend}"
if [ -z "${engine}" ] || [ "${engine}" != "${backend}" ] || [ "${engine}" != "${frontend}" ]; then
  echo "version mismatch" >&2
  exit 1
fi
if [ "${GITHUB_REF_TYPE:-}" = "tag" ] && [ "${GITHUB_REF_NAME:-}" != "v${engine}" ]; then
  echo "tag ${GITHUB_REF_NAME} does not match version v${engine}" >&2
  exit 1
fi
