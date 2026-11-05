#!/bin/sh
# Preserve the installed distribution/site hook; do not modify package files.
if [ -x /usr/share/sddm/scripts/Xsetup ]; then
    /usr/share/sddm/scripts/Xsetup
fi
# Inherit DISPLAY/XAUTHORITY from SDDM; never assume :0.
/usr/bin/xset -dpms
/usr/bin/xset s off
/usr/bin/xset s noblank
