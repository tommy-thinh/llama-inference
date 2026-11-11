#!/usr/bin/env bash
# A logical backup of one local database; never deletes or overwrites a backup.
set -euo pipefail
umask 077

fail() { printf '%s\n' "$1" >&2; exit 1; }
[[ $# == 1 ]] || fail 'Usage: backup-database.sh PRIVATE_DESTINATION_DIRECTORY'
[[ ${PGHOST:-} == 127.0.0.1 || ${PGHOST:-} == ::1 ]] || fail 'PGHOST must be loopback'
[[ ${PGDATABASE:-} =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ && ${PGUSER:-} =~ ^[a-zA-Z_][a-zA-Z0-9_]*$ ]] || fail 'Set simple PGDATABASE and PGUSER names, not connection strings'
[[ ${PGPORT:-5432} =~ ^[0-9]+$ ]] || fail 'PGPORT must be numeric'
[[ -n ${PGPASSFILE:-} && -f $PGPASSFILE && ! -L $PGPASSFILE ]] || fail 'Set a regular PGPASSFILE'
[[ $(stat -c %u "$PGPASSFILE") == "$EUID" && $(stat -c %a "$PGPASSFILE") == 600 ]] || fail 'PGPASSFILE must be owned by this user with mode 0600'
unset PGPASSWORD PGSERVICE PGSERVICEFILE PGHOSTADDR
export PGCONNECT_TIMEOUT=5

destination=$1
[[ ! -L $destination ]] || fail 'Destination must not be a symlink'
mkdir -p -- "$destination"
[[ $(stat -c %u "$destination") == "$EUID" && $(stat -c %a "$destination") == 700 ]] || fail 'Destination must be owned by this user with mode 0700'
temporary=$(mktemp "$destination/.backup-XXXXXXXX.part")
trap 'rm -f -- "$temporary"' EXIT
pg_dump --no-password --format=custom --no-owner --no-acl --lock-wait-timeout=10s --file="$temporary"
[[ -s $temporary ]] || fail 'Backup is empty'
pg_restore --list "$temporary" > /dev/null
final="$destination/llama-$(date -u +%Y%m%dT%H%M%SZ)-${temporary##*/}.dump"
# Hard-linking publishes only the complete checked file and refuses collisions.
ln -- "$temporary" "$final"
printf '%s\n' "$final"
