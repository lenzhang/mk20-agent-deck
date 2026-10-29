#!/bin/sh
# Download the Waveshare MK20 (B) product photos used by the simulator (mk20sim) as background.
# They are Waveshare's copyright, so they are NOT committed; see sources.md for the URLs.
set -eu
cd "$(dirname "$0")"
get() { [ -s "$1" ] || curl -fsSL -o "$1" "$2"; echo "$1"; }
get mk20-b-top-unlit.jpg https://www.waveshare.com/media/catalog/product/cache/1/image/800x800/9df78eab33525d08d6e5fb8d27136e95/m/k/mk20-b-2.jpg
get mk20-b-lit-hero.jpg  https://www.waveshare.com/media/catalog/product/cache/1/image/800x800/9df78eab33525d08d6e5fb8d27136e95/m/k/mk20-b-1.jpg
get mk20-dimensions.jpg  https://www.waveshare.com/img/devkit/LCD/MK20-B/MK20-B-details-size.jpg
get mk20-b-rear-edge.jpg https://www.waveshare.com/media/catalog/product/cache/1/image/800x800/9df78eab33525d08d6e5fb8d27136e95/m/k/mk20-b-7.jpg
