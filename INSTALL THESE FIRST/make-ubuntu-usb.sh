#!/bin/bash
# ============================================================================
#  Writes the Ubuntu 26.04.1 installer onto a USB stick (8 GB or bigger).
#  EVERYTHING ON THE STICK IS ERASED.
#  Run on any Linux machine:   bash "INSTALL THESE FIRST/make-ubuntu-usb.sh"
#  On Windows use Rufus or balenaEtcher with the .iso in the ubuntu-iso folder.
# ============================================================================
set -u
HERE="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
ISO="$HERE/ubuntu-iso/ubuntu-26.04.1-desktop-amd64.iso"
B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; X=$'\e[0m'

[ -f "$ISO" ] || { echo "${R}Can't find $ISO${X}"; exit 1; }

echo "${B}1. Checking the Ubuntu image isn't damaged...${X} (about a minute)"
want=$(grep 'ubuntu-26.04.1-desktop-amd64.iso' "$HERE/ubuntu-iso/SHA256SUMS" | cut -d' ' -f1)
have=$(sha256sum "$ISO" | cut -d' ' -f1)
[ "$want" = "$have" ] && echo "   ${G}✓ image is intact${X}" || { echo "   ${R}✗ the image is corrupted — don't use it${X}"; exit 1; }

echo
echo "${B}2. USB drives plugged in right now:${X}"
mapfile -t DEVS < <(lsblk -d -p -n -o NAME,TRAN | awk '$2=="usb"{print $1}')
if [ ${#DEVS[@]} -eq 0 ]; then echo "   none found. Plug in a USB stick and run this again."; exit 1; fi
for d in "${DEVS[@]}"; do
  printf "   ${B}%-10s${X} %6s  %s %s\n" "$d" "$(lsblk -d -n -o SIZE "$d")" "$(lsblk -d -n -o VENDOR "$d" | xargs)" "$(lsblk -d -n -o MODEL "$d" | xargs)"
  lsblk -n -o NAME,SIZE,LABEL,MOUNTPOINTS "$d" | tail -n +2 | sed 's/^/                 /'
done
echo
read -r -p "Type the name of the stick to ERASE (e.g. ${DEVS[0]}), or press Enter to cancel: " DEV
[ -z "$DEV" ] && { echo "Cancelled."; exit 0; }
printf '%s\n' "${DEVS[@]}" | grep -qx "$DEV" || { echo "${R}$DEV is not one of the USB drives listed. Cancelled.${X}"; exit 1; }
size=$(lsblk -b -d -n -o SIZE "$DEV"); [ "$size" -ge 7000000000 ] || { echo "${R}That stick is too small (need 8 GB+).${X}"; exit 1; }
if lsblk -n -o MOUNTPOINTS "$DEV" | grep -qE '^/$|^/boot|^/home'; then echo "${R}$DEV holds this computer's system. Refusing.${X}"; exit 1; fi

echo
echo "${R}${B}Everything on $DEV ($(lsblk -d -n -o SIZE "$DEV") $(lsblk -d -n -o MODEL "$DEV" | xargs)) will be destroyed.${X}"
read -r -p "Type ERASE to continue: " OK
[ "$OK" = "ERASE" ] || { echo "Cancelled."; exit 0; }

for part in $(lsblk -n -p -o NAME "$DEV" | tail -n +2); do udisksctl unmount -b "$part" >/dev/null 2>&1; done
echo "${B}3. Writing Ubuntu to $DEV${X} (5-15 minutes; you'll be asked for your password)"
pkexec dd if="$ISO" of="$DEV" bs=4M status=progress oflag=direct conv=fsync || { echo "${R}Writing failed.${X}"; exit 1; }
sync
echo
echo "${G}${B}Done.${X} The stick is a Ubuntu installer now. Next:"
echo "  1. Plug it into the new computer, turn it on, and press the boot-menu key"
echo "     (usually F12, F11, F8, Esc or Del) — pick the USB stick."
echo "  2. Choose 'Install Ubuntu'. Tick 'Install third-party software for graphics' if offered."
echo "  3. When Ubuntu is installed: copy the joshua folder over and run"
echo "     bash \"INSTALL THESE FIRST/install.sh\""
