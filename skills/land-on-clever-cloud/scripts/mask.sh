#!/usr/bin/env bash
# mask.sh — mask credentials in any text before showing it (stdin -> stdout), e.g. logs:
#
#   clever logs --alias web --since 15m --until now | tail -n 60 | bash <skill>/scripts/mask.sh
#
# The same masker as every script of the skill (lib/common.py mask()): URLs with credentials, JWT,
# provider keys, Bearer values, --password/-p options, NAME=value when NAME looks secret, long
# opaque strings. Variable names, Clever ids and git SHAs stay readable. Exit 0.

set -u
. "$(cd "$(dirname "$0")" && pwd)/lib.sh"
case "${1:-}" in -h|--help) loc_help "$0"; exit 0 ;; esac
loc_mask
