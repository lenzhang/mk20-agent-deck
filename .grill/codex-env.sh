#!/bin/bash
# Explicit 112-US proxy env for codex (mihomo mixed-port 7890 -> rule openai.com -> AI -> US-Exit-Hy2)
export HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 ALL_PROXY=http://127.0.0.1:7890
export https_proxy=$HTTPS_PROXY http_proxy=$HTTP_PROXY all_proxy=$ALL_PROXY
export NO_PROXY=127.0.0.1,localhost,::1 no_proxy=127.0.0.1,localhost,::1
exec "$@"
