#!/bin/bash
# Update only the reviewed LAB runtime files, preserving the current units.
set -Eeuo pipefail

if [[ $EUID -ne 0 ]]; then
  echo 'Dieses LAB-Update benötigt root.' >&2
  exit 1
fi
if [[ ${1:-} != --locked ]]; then
  exec python3 -B /home/lab-admin/cisco-lab/deployment/apps/guard_runtime_update.py
fi

source_root=/home/lab-admin/cisco-lab
runtime_root=/opt/cisco-lab
mapfile -t files < <(python3 -B -c 'import runpy; print(*runpy.run_path("/home/lab-admin/cisco-lab/deployment/apps/sync_runtime.py")["FILES"], sep="\n")')
(( ${#files[@]} > 0 )) || exit 1
systemctl is-active --quiet cisco-lab-dashboard.service
systemctl is-active --quiet cisco-lab-controller.timer
[[ $(systemctl show -p ActiveState --value cisco-lab-controller.service) == inactive ]] || exit 1
retired_timer=maintenance/systemd/cisco-lab-maintenance.timer
teacher_installer=deployment/teacher/teacher-installer.py

backup_dir=$(mktemp -d /tmp/cisco-lab-runtime.XXXXXXXX)
stopped=0
retired_present=0
finish() {
  status=$?
  trap - EXIT
  if (( status != 0 && stopped == 1 )); then
    for name in "${files[@]}"; do
      if [[ -f $backup_dir/$name ]]; then
        cp -p -- "$backup_dir/$name" "$runtime_root/$name"
      else
        rm -f -- "$runtime_root/$name"
      fi
    done
    if (( retired_present == 1 )); then
      cp -p -- "$backup_dir/$retired_timer" "$runtime_root/$retired_timer"
    fi
    if [[ -f $backup_dir/$teacher_installer ]]; then
      cp -p -- "$backup_dir/$teacher_installer" "$runtime_root/$teacher_installer"
    fi
  fi
  if (( stopped == 1 )); then
    systemctl start cisco-lab-dashboard.service cisco-lab-controller.timer
  fi
  rm -r -- "$backup_dir"
  exit "$status"
}
trap finish EXIT

for name in "${files[@]}"; do
  [[ -f $source_root/$name && ! -L $source_root/$name ]] || exit 1
  if [[ -e $runtime_root/$name || -L $runtime_root/$name ]]; then
    [[ -f $runtime_root/$name && ! -L $runtime_root/$name ]] || exit 1
    mkdir -p -- "$backup_dir/$(dirname "$name")"
    cp -p -- "$runtime_root/$name" "$backup_dir/$name"
  fi
done
if [[ -e $runtime_root/$retired_timer || -L $runtime_root/$retired_timer ]]; then
  [[ -f $runtime_root/$retired_timer && ! -L $runtime_root/$retired_timer ]] || exit 1
  mkdir -p -- "$backup_dir/$(dirname "$retired_timer")"
  cp -p -- "$runtime_root/$retired_timer" "$backup_dir/$retired_timer"
  retired_present=1
fi
if [[ -e $runtime_root/$teacher_installer || -L $runtime_root/$teacher_installer ]]; then
  [[ -f $runtime_root/$teacher_installer && ! -L $runtime_root/$teacher_installer ]] || exit 1
  mkdir -p -- "$backup_dir/$(dirname "$teacher_installer")"
  cp -p -- "$runtime_root/$teacher_installer" "$backup_dir/$teacher_installer"
fi

systemctl stop cisco-lab-controller.timer cisco-lab-dashboard.service
stopped=1
python3 -B "$source_root/deployment/apps/sync_runtime.py"
systemctl start cisco-lab-dashboard.service cisco-lab-controller.timer
stopped=0
systemctl is-active --quiet cisco-lab-dashboard.service
systemctl is-active --quiet cisco-lab-controller.timer
systemctl show -p ActiveState -p UnitFileState cisco-lab-dashboard.service cisco-lab-controller.timer
