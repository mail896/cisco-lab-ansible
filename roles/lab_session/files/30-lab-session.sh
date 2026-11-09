# Sourced before Plasma starts; never exit the parent session shell.
if /usr/local/libexec/cisco-lab/session-runtime --prepare-autostart; then
    case ":${XDG_CONFIG_DIRS:-/etc/xdg}:" in
        *:/etc/cisco-lab/session-xdg:*) ;;
        *) XDG_CONFIG_DIRS="/etc/cisco-lab/session-xdg:${XDG_CONFIG_DIRS:-/etc/xdg}" ;;
    esac
    export XDG_CONFIG_DIRS
    if ! /usr/bin/systemctl --user daemon-reload; then
        printf '%s\n' 'LAB session: Autostart-Einheiten konnten nicht neu eingelesen werden.' >&2
    fi
fi
