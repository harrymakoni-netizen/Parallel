#!/bin/sh
# Runs a build step, trusting an extra certificate bundle when one is supplied as the
# optional "ca" build secret (for builds behind a TLS-inspecting proxy). Nothing is stored
# in the image: the secret is mounted only for the duration of the step.
set -eu
if [ -s /run/secrets/ca ]; then
  export SSL_CERT_FILE=/run/secrets/ca REQUESTS_CA_BUNDLE=/run/secrets/ca PIP_CERT=/run/secrets/ca \
    NODE_EXTRA_CA_CERTS=/run/secrets/ca
fi
exec "$@"
