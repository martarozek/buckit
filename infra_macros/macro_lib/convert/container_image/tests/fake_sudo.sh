#!/bin/bash -uex
#
# `tests/test_subvolume_garbage_collector.py` uses this fake in order to
# avoid instantiating actual btrfs subvolumes to delete.

die() {
  echo "$@" 1>&2
  exit 1
}

[[ "$#" == "4" ]] || die "Bad arg count:" "$@"
[[ "$1" == "btrfs" ]] || die "Bad arg 1: $1"
[[ "$2" == "subvolume" ]] || die "Bad arg 2: $2"
[[ "$3" == "delete" ]] || die "Bad arg 3: $3"

rmdir "$4"