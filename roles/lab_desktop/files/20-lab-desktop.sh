# Sourced by startplasma BEFORE plasmashell. Never exit the parent shell here.
# Only normal members of the LAB group receive these policies.
if /usr/local/libexec/cisco-lab/prepare-desktop; then
    case ":${XDG_CONFIG_DIRS:-/etc/xdg}:" in
        *:/etc/cisco-lab/xdg:*) ;;
        *) XDG_CONFIG_DIRS="/etc/cisco-lab/xdg:${XDG_CONFIG_DIRS:-/etc/xdg}" ;;
    esac
    export XDG_CONFIG_DIRS
    if [ -d /etc/cisco-lab/settings-menu/applications ]; then
        case ":${XDG_DATA_DIRS:-/usr/local/share:/usr/share}:" in
            *:/etc/cisco-lab/settings-menu:*) ;;
            *) XDG_DATA_DIRS="/etc/cisco-lab/settings-menu:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" ;;
        esac
        export XDG_DATA_DIRS
    fi
    # PAM may already have started the user manager and its XDG generator.
    # The helper now writes standard per-user entries. Regenerate their units
    # before xdg-desktop-autostart.target runs; no service or app is started here.
    if ! /usr/bin/systemctl --user daemon-reload; then
        printf '%s\n' 'LAB desktop: Autostart-Einheiten konnten nicht neu eingelesen werden; Anmeldetest nicht bestanden.' >&2
    fi
fi
