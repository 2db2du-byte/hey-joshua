JOSHUA — HOW TO MOVE HIM TO ANOTHER COMPUTER
=============================================

Everything Joshua is lives in the "joshua" folder: his brain, voice, ears,
memory of you, bookshelf, image engine, Ubuntu itself, and this installer.
No internet is needed for any of the steps below.


A) THE NEW COMPUTER ALREADY HAS UBUNTU 26.04 (or Xubuntu/Kubuntu 26.04)
------------------------------------------------------------------------
  1. Copy the whole "joshua" folder onto it.
     Carry it on a Linux-formatted drive (ext4). Windows formats (NTFS/exFAT) break him.
  2. Open a terminal in the joshua folder and run:
        bash "INSTALL THESE FIRST/install.sh"
  3. If it says REBOOT (it just installed the graphics driver): reboot, run it again.
  4. Open his HUD:  joshua-hud   (or browse to http://127.0.0.1:8899)
     Or talk to him in a terminal:  joshua


B) THE NEW COMPUTER IS BLANK, BROKEN, OR RUNS WINDOWS
------------------------------------------------------
  1. Make a Ubuntu USB stick (8 GB or bigger — it gets ERASED):
       from any Linux machine:  bash "INSTALL THESE FIRST/make-ubuntu-usb.sh"
       from Windows:            use Rufus or balenaEtcher with
                                ubuntu-iso/ubuntu-26.04.1-desktop-amd64.iso
     Joshua can do this for you if he's running on another computer: ask him.
  2. Plug the stick into the new computer, power on, press the boot-menu key
     (usually F12, F11, F8, Esc or Del) and pick the USB stick.
  3. Choose "Install Ubuntu". When asked, tick
     "Install third-party software for graphics and Wi-Fi hardware".
     WARNING: "Erase disk and install Ubuntu" wipes that computer's drive.
  4. When Ubuntu is installed, continue with part A.

  Joshua can't help during step 3 — he needs Ubuntu to run. Once part A is
  done he can help with everything else on that machine.


WHAT HE ADAPTS TO ON HIS OWN
-----------------------------
  NVIDIA card with 8 GB+   full mode: big brain, deep thinking, picture rendering in the HUD
  NVIDIA card with 4-7 GB  lighter mode: smaller brain, still thinks
  no NVIDIA card           CPU mode: smaller brain, slow, deep thinking only
                           when you say "think hard". No picture studio.
  He tells you which mode he's in when he starts.

  Graphics drivers: GTX 16 / RTX 20-series and newer — the installer puts in
  the driver Joshua carries. Older cards (GTX 10-series and before) — use the
  "third-party software" tick box while installing Ubuntu (step B3).


WHAT'S IN THIS FOLDER
---------------------
  install.sh               the installer (safe to run again any time)
  make-ubuntu-usb.sh       writes the Ubuntu USB stick
  ubuntu-iso/              Ubuntu 26.04.1 installer image + checksums
  debs/                    Ubuntu packages Joshua needs: Python, library server,
                           audio tools, compiler, NVIDIA driver + matching kernel
  services/                background services: brain, bookshelf, image engine, studio
  requirements-*.txt       exact Python parts (only used for a rebuild, which needs internet)


WHAT THE NEW COMPUTER NEEDS
---------------------------
  64-bit Intel/AMD PC, 8 GB+ RAM (16 GB better), about 125 GB free space.
  An NVIDIA card makes him fast. Without one he works, just slowly.


HIS MEMORY
----------
  joshua/memory/facts.md   what he knows about you
  joshua/memory/rules.md   things you've taught him
  Plain text. Read them, edit them, delete a line to make him forget it.
