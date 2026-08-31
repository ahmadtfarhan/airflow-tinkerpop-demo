#!/bin/bash
# Archive or restore the generated data directories.
#
# Use this instead of `rm -rf data/corpus data/results data/cache` before a
# clean rerun. data/cache in particular holds ~215 already-paid-for embedding
# vectors; deleting it costs a fifth of the daily free-tier allowance to
# rebuild, for no benefit, since the corpus is deterministic.
#
# data/seed is never touched -- those are the committed offline-fallback
# artifacts the demo depends on.
#
#   ./scripts/archive_data.sh archive          # stash and clear
#   ./scripts/archive_data.sh list             # show what is stashed
#   ./scripts/archive_data.sh restore          # bring back the newest
#   ./scripts/archive_data.sh restore <name>   # bring back a specific one
set -euo pipefail
cd "$(dirname "$0")/.."

DIRS=(corpus results cache index)
ARCHIVE_ROOT="data/_archive"
ACTION="${1:-archive}"

human_size() { du -sh "$1" 2>/dev/null | cut -f1; }

case "$ACTION" in
  archive)
    STAMP="${2:-$(date +%Y%m%d-%H%M%S)}"
    DEST="$ARCHIVE_ROOT/$STAMP"
    moved=0
    for d in "${DIRS[@]}"; do
      if [ -d "data/$d" ]; then
        mkdir -p "$DEST"
        echo "  archiving data/$d ($(human_size "data/$d"))"
        mv "data/$d" "$DEST/$d"
        moved=$((moved + 1))
      fi
    done
    if [ "$moved" -eq 0 ]; then
      echo "Nothing to archive -- data/{$(IFS=,; echo "${DIRS[*]}")} are all absent."
      exit 0
    fi
    # Record what this snapshot was, so a later restore is an informed choice.
    {
      echo "archived_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "git_commit:  $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
      for d in "${DIRS[@]}"; do
        [ -d "$DEST/$d" ] && echo "$d: $(find "$DEST/$d" -type f | wc -l | tr -d ' ') files, $(human_size "$DEST/$d")"
      done
    } > "$DEST/MANIFEST.txt"
    echo
    echo "Archived to $DEST"
    sed 's/^/  /' "$DEST/MANIFEST.txt"
    echo
    echo "Restore with: ./scripts/archive_data.sh restore $STAMP"
    ;;

  list)
    if [ ! -d "$ARCHIVE_ROOT" ] || [ -z "$(ls -A "$ARCHIVE_ROOT" 2>/dev/null)" ]; then
      echo "No archives."
      exit 0
    fi
    for d in "$ARCHIVE_ROOT"/*/; do
      echo "$(basename "$d")  ($(human_size "$d"))"
      [ -f "$d/MANIFEST.txt" ] && sed 's/^/    /' "$d/MANIFEST.txt"
    done
    ;;

  restore)
    STAMP="${2:-}"
    if [ -z "$STAMP" ]; then
      STAMP=$(ls -1 "$ARCHIVE_ROOT" 2>/dev/null | sort | tail -1 || true)
    fi
    SRC="$ARCHIVE_ROOT/$STAMP"
    if [ -z "$STAMP" ] || [ ! -d "$SRC" ]; then
      echo "No such archive: ${STAMP:-<none found>}" >&2
      echo "Available:" >&2
      ls -1 "$ARCHIVE_ROOT" 2>/dev/null | sed 's/^/  /' >&2 || echo "  (none)" >&2
      exit 1
    fi
    # Never clobber live data silently -- park it first.
    for d in "${DIRS[@]}"; do
      if [ -d "data/$d" ]; then
        echo "  data/$d exists; archiving it first"
        "$0" archive "pre-restore-$(date +%Y%m%d-%H%M%S)" >/dev/null
        break
      fi
    done
    for d in "${DIRS[@]}"; do
      if [ -d "$SRC/$d" ]; then
        echo "  restoring data/$d"
        mv "$SRC/$d" "data/$d"
      fi
    done
    rmdir "$SRC" 2>/dev/null || true
    echo
    echo "Restored from $STAMP"
    ;;

  *)
    echo "usage: $0 {archive|list|restore [name]}" >&2
    exit 2
    ;;
esac
