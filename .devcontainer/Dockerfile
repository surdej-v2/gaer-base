# The Codespace image: Python 3.13 (as in the production Dockerfile); Node 22 and Docker come as features.
FROM mcr.microsoft.com/devcontainers/python:1-3.13-bookworm

# The base image ships Yarn's apt source, whose signing key no longer verifies (NO_PUBKEY 62D54FD4003F6525).
# Every apt-get update then fails, and with it the docker-in-docker feature. Gær uses npm, not Yarn.
RUN grep -rlE 'yarnpkg' /etc/apt/sources.list.d/ 2>/dev/null | xargs -r rm -f
