# RaspberryPi-USB-Flasher-ARM-based
of course once in a while we need this also , to make bootable usb stick using raspberry pi 
this started as a personal project as my main laptop got shutdown (while i was uisng linux-ubuntu) and cannot turned back on so i didnt had spare laptop or desktop so i had to rely on raspberry , but for some reason other apps (bootable makers) didnt worked out with me , so when i am back to original state first thing i did is making a python program for format | iso writer | make a bootable usb. 

so here it goes guys!
have some fun!
and please update me for bugs | issues | new ideas , i am happy to implement them....

All hail Astartes!

USB Formatter GUI for Raspberry Pi OS

Usage

- Recommended: run with root privileges to avoid repeated password prompts:

```bash
sudo python3 format_usb_gui.py
```

- If you run as non-root, the script will invoke `sudo` for formatting and unmounting operations.

Dependencies (install on Raspberry Pi OS):

```bash
sudo apt update
sudo apt install -y python3-tk dosfstools exfat-utils ntfs-3g btrfs-progs xfsprogs
```

Notes

- This tool lists block devices via `lsblk`. Select the correct device (e.g., `/dev/sda`, `/dev/sdb`) before formatting.
- Formatting is destructive. Double-check the selected device.
- The script attempts to unmount any mounted partitions of the device before formatting.

Supported filesystems

- Detected dynamically based on available `mkfs` binaries (e.g., `mkfs.ext4`, `mkfs.vfat`, `mkfs.exfat`, `mkfs.ntfs`, `mkfs.xfs`, `mkfs.btrfs`).

License

Provided as-is for personal use. Use at your own risk.
