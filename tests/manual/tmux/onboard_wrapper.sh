#!/usr/bin/env bash

set -uo pipefail

config_dir="$1"
bin_path="$2"

env REVKA_CONFIG_DIR="$config_dir" "$bin_path" onboard
status=$?
printf '\nEXIT_STATUS=%s\n' "$status"
sleep 5
