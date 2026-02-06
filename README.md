# RaspberryPi-USB-Flasher-ARM-based
<img width="2400" height="1792" alt="raspi_usb_bootable" src="https://github.com/user-attachments/assets/5f8275db-a7f0-4cf4-ac57-e18a660160b7" />


#before download
#imagers not yet supported , only bootable iso only


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

- update
- Windows ISO Flashing Capability - Implementation Summary
I've successfully added Windows ISO flashing support to your application with the following features:

New Functions Added:
detect_windows_iso(iso_path) - Detects if an ISO is Windows and identifies the version (7, 10, or 11) based on filename analysis.

write_windows_iso_to_device(devname, iso_path, log, progress_cb) - Comprehensive Windows ISO writer that:

Automatically detects ISO size and chooses appropriate filesystem (FAT32 for <4GB, exFAT for ≥4GB)
Supports Windows 7, 10, and 11
Creates MBR partition table (required for Windows)
Formats partition with correct filesystem
Mounts the ISO and copies all contents to USB
Handles all mount/unmount operations
Provides progress updates throughout the process
GUI Enhancements:
New "Write Windows ISO" Button - Added to the interface next to the regular ISO write button
on_write_windows_iso() Method - Handles the Windows ISO write workflow:
Device selection validation
Confirmation dialog with supported versions
ISO file selection
Windows ISO auto-detection with override option
Background thread execution
Completion notification
Key Features:
✅ Automatic filesystem selection - Uses exFAT for large ISOs (>4GB), FAT32 for smaller ones
✅ Windows 7, 10, and 11 support - Auto-detects version from filename
✅ Progress tracking - Real-time progress updates during the flashing process
✅ Safe operations - Unmounts all existing partitions before starting
✅ Error handling - Comprehensive error messages and fallback options
✅ ISO mounting - Extracts ISO contents to USB using mount + copy method

License

Provided as-is for personal use. Use at your own risk.
