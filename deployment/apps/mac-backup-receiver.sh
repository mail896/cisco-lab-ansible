#!/bin/sh
# Forced command for the APP LAB backup key. Install as
# /Volumes/Repo/cisco-lab/.receiver.sh on the macOS backup host.
set -efu
PATH=/usr/bin:/bin
LC_ALL=C
export PATH LC_ALL
umask 077

directory=/Volumes/Repo/cisco-lab
[ -d "$directory" ] && [ ! -L "$directory" ] || exit 1
cd "$directory"

# Disable glob expansion before splitting SSH_ORIGINAL_COMMAND into fixed fields.
set -f
set -- ${SSH_ORIGINAL_COMMAND-}
[ "$#" -ge 1 ] || exit 1
action=$1

if [ "$action" = check ]; then
    [ "$#" -eq 1 ] || exit 1
    printf 'ready\n'
    exit 0
fi

[ "$#" -eq 3 ] || exit 1
name=$2
expected=$3
printf '%s\n' "$name" | /usr/bin/grep -Eq '^CISCO-LAB-APP-Konfiguration-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}\.tar\.gz\.gpg$' || exit 1
printf '%s\n' "$expected" | /usr/bin/grep -Eq '^[0-9a-f]{64}$' || exit 1

if [ "$action" = put ]; then
    [ ! -e "$name" ] && [ ! -L "$name" ] || exit 1
    temporary=".$name.partial.$$"
    digest_temporary=".$name.sha256.partial.$$"
    [ ! -e "$temporary" ] && [ ! -L "$temporary" ] || exit 1
    [ ! -e "$digest_temporary" ] && [ ! -L "$digest_temporary" ] || exit 1
    trap '/bin/rm -f "$temporary" "$digest_temporary"' 0 1 2 3 15
    ulimit -f 2097152
    /bin/cat > "$temporary"
    actual=$(/usr/bin/shasum -a 256 "$temporary")
    actual=${actual%% *}
    [ "$actual" = "$expected" ] || exit 1
    printf '%s  %s\n' "$actual" "$name" > "$digest_temporary"
    /bin/mv "$temporary" "$name"
    /bin/mv "$digest_temporary" "$name.sha256"
    printf 'SHA256:%s\n' "$actual"
elif [ "$action" = delete ] || [ "$action" = get ]; then
    if [ ! -e "$name" ] && [ ! -e "$name.sha256" ]; then
        if [ "$action" = delete ]; then
            printf 'absent\n'
            exit 0
        fi
        exit 1
    fi
    [ -f "$name" ] && [ ! -L "$name" ] || exit 1
    [ -f "$name.sha256" ] && [ ! -L "$name.sha256" ] || exit 1
    actual=$(/usr/bin/shasum -a 256 "$name")
    actual=${actual%% *}
    [ "$actual" = "$expected" ] || exit 1
    [ "$(/bin/cat "$name.sha256")" = "$expected  $name" ] || exit 1
    if [ "$action" = get ]; then
        /bin/cat "$name"
    else
        /bin/rm "$name" "$name.sha256"
        printf 'deleted\n'
    fi
else
    exit 1
fi
