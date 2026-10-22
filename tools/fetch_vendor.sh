#!/bin/sh
set -eu
project_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

checkout_dependency() {
    directory=$1
    repository=$2
    revision=$3
    target="$project_root/vendor/$directory"
    if [ -e "$target" ]; then
        actual=$(git -C "$target" rev-parse HEAD)
        if [ "$actual" != "$revision" ] || [ -n "$(git -C "$target" status --porcelain)" ]; then
            printf 'Existing dependency differs from the pinned clean checkout: %s\n' "$directory" >&2
            exit 1
        fi
    else
        mkdir -p "$project_root/vendor"
        git clone --no-checkout "$repository" "$target"
        git -C "$target" checkout --detach "$revision"
    fi
    printf '%s: %s\n' "$directory" "$revision"
}

checkout_dependency cardboard https://github.com/googlevr/cardboard.git 5969239e7c87f4cd64c8ec170ce1e7f4eb559e37
checkout_dependency tinygltf https://github.com/syoyo/tinygltf.git b956fa3e9deeb7ee7f0f22c08bda9b3a684a98dc
