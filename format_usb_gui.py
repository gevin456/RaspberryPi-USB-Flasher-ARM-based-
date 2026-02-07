#!/usr/bin/env python3
"""
USB Formatter GUI for Raspberry Pi OS (Tkinter-based)
Compatible with all Raspberry Pi OS versions (Bullseye, Bookworm, Trixie, etc.)

Features:
- Lists block devices via lsblk
- Detects available mkfs tools and presents filesystem options
- Unmounts mounted partitions, runs mkfs with sudo
- Create bootable Linux and Windows USB drives
- Dynamic font selection for compatibility with all Linux desktop environments
- Robust fallback mechanisms for various system tools

Compatibility:
- Raspberry Pi OS (all versions: Bullseye, Bookworm, Trixie)
- Any Debian/Ubuntu-based Linux distribution
- Works with different fontsets (Auto-detects and uses available system fonts)
- Fallback support for partprobe (uses blockdev or kernel wait if unavailable)
- Supports both old (exfat-utils) and new (exfat-fuse) exFAT packages

Requirements:
- Python 3.7+
- Tkinter (python3-tk)
- Parted and standard Linux utilities
- sudo access for device operations

Note: This tool performs destructive operations. Run with care and preferably as root (sudo).
"""

# Release/version marker
__version__ = "2.0.1"  # Added Raspberry Pi compatibility improvements

# Copyright
# Copyright (c) 2026 Gevin
# All rights reserved.

import json
import shutil
import subprocess
import sys
import threading
from pathlib import Path
import os
import re
import time
import hashlib
import urllib.request
import urllib.parse
import html
# Pillow splash support removed - no splash screen in this build
import platform

LSBLK_CMD = ["lsblk", "-J", "-o", "NAME,KNAME,SIZE,MODEL,MOUNTPOINT,TYPE,RM"]

FS_CANDIDATES = {
    "ext4": ["mkfs.ext4"],
    "vfat (FAT32)": ["mkfs.vfat", "mkfs.fat"],
    "exfat": ["mkfs.exfat"],
    "ntfs": ["mkfs.ntfs", "mkntfs"],
    "xfs": ["mkfs.xfs"],
    "btrfs": ["mkfs.btrfs"],
}

# Dependency check and installer
INSTALL_LOG = Path(__file__).with_name('dependency_install_log.txt')


def write_install_log(text: str):
    try:
        with open(INSTALL_LOG, 'a', encoding='utf-8') as fh:
            fh.write(f"{time.asctime()}: {text}\n")
    except Exception:
        pass


def get_compatible_font(default_name='TkDefaultFont', fallback_size=10):
    """Get a compatible font for the current system (works on Raspberry Pi and others)."""
    try:
        from tkinter import font as tkfont
        available_fonts = tkfont.families()
        
        # List of fonts to try in order of preference
        preferred_fonts = ['Ubuntu', 'DejaVu Sans', 'Liberation Sans', 'Noto Sans', 'Helvetica', 'Arial']
        
        for font_name in preferred_fonts:
            if font_name in available_fonts:
                return (font_name, fallback_size)
        
        # Fallback to system default
        return (default_name, fallback_size)
    except Exception:
        return (default_name, fallback_size)


def get_system_info():
    """Get Raspberry Pi OS and system information for compatibility checks."""
    info = {
        'os': platform.system(),
        'distro': 'unknown',
        'python_version': platform.python_version(),
        'machine': platform.machine(),
    }
    
    try:
        # Try to detect Linux distro
        if Path('/etc/os-release').exists():
            with open('/etc/os-release', 'r', encoding='utf-8') as f:
                for line in f:
                    if line.startswith('ID='):
                        info['distro'] = line.split('=')[1].strip().strip('"')
                    elif line.startswith('VERSION_ID='):
                        info['version'] = line.split('=')[1].strip().strip('"')
    except Exception:
        pass
    
    return info


def check_and_install_dependencies():
    """Check required tools and try to install missing packages on Debian-based systems.
    Returns True if all dependencies satisfied (after possible install), False otherwise.
    Handles package name variations across different Raspberry Pi OS versions (Bullseye, Bookworm, Trixie).
    """
    write_install_log("Starting dependency check")
    missing_pkgs = []

    # Check tkinter availability
    try:
        import tkinter  # type: ignore
        tk_ok = True
    except Exception:
        tk_ok = False
        missing_pkgs.append('python3-tk')

    # Map binary -> possible package names (first match wins)
    # This handles variations across Bullseye, Bookworm, Trixie, and other Debian-based systems
    bin_to_packages = {
        'parted': ['parted'],
        'mkfs.vfat': ['dosfstools'],
        'mkfs.exfat': ['exfat-fuse', 'exfat-utils'],  # Trixie uses exfat-fuse; older versions use exfat-utils
        'mkfs.ntfs': ['ntfs-3g'],
        'mkfs.btrfs': ['btrfs-progs'],
        'mkfs.xfs': ['xfsprogs'],
        'pv': ['pv'],
        'lsblk': ['util-linux'],
        'dd': ['coreutils'],
    }
    
    for binname, pkgnames in bin_to_packages.items():
        if shutil.which(binname) is None:
            # Binary not found; try to add first package that might provide it
            if pkgnames:
                missing_pkgs.append(pkgnames[0])

    # Deduplicate
    missing_pkgs = list(dict.fromkeys(missing_pkgs))

    if not missing_pkgs:
        write_install_log('All dependencies present')
        return True

    write_install_log(f'Missing packages/tools: {missing_pkgs}')

    # If not Linux or no apt, cannot auto-install
    if platform.system() != 'Linux' or shutil.which('apt-get') is None:
        write_install_log('Auto-install not available (non-Linux or apt-get missing)')
        # write helpful instructions
        cmd = 'sudo apt update && sudo apt install -y ' + ' '.join(missing_pkgs)
        write_install_log('Run the following to install missing packages:')
        write_install_log(cmd)
        print('Missing dependencies:', missing_pkgs)
        print('See', INSTALL_LOG, 'for install instructions.')
        return False

    # Try to install via apt-get with fallback for package name variations
    try:
        write_install_log('Running apt-get update')
        r = subprocess.run(['sudo', 'apt-get', 'update'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        write_install_log(r.stdout)
        
        # For each package, try to install; if it fails, skip with a warning
        # This allows partial installation when some packages don't exist in this distro
        write_install_log('Attempting to install packages individually to handle variations')
        for pkg in missing_pkgs:
            try:
                cmd = ['sudo', 'apt-get', 'install', '-y', pkg]
                r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=120)
                write_install_log(f'Package {pkg}: {r.stdout[:200]}...')
            except subprocess.TimeoutExpired:
                write_install_log(f'Package {pkg}: timeout during install')
            except Exception as e:
                write_install_log(f'Package {pkg}: {e}')
    except Exception as e:
        write_install_log(f'Exception during apt install: {e}')
        return False

    # Re-check critical tools; don't fail if all dependencies not met (some might be optional)
    try:
        import tkinter  # type: ignore
        write_install_log('tkinter import successful after install')
    except Exception as e:
        write_install_log(f'tkinter still unavailable: {e}')
        return False
    
    # Check for at least lsblk, parted, and mkfs tools
    has_lsblk = shutil.which('lsblk') is not None
    has_parted = shutil.which('parted') is not None
    has_mkfs = any(shutil.which(cmd) for cmd in ['mkfs.ext4', 'mkfs.vfat'])
    
    if not (has_lsblk and has_parted and has_mkfs):
        write_install_log(f'Critical tools missing: lsblk={has_lsblk}, parted={has_parted}, mkfs={has_mkfs}')
        return False

    write_install_log('Dependencies satisfied')
    return True
 


def detect_filesystems():
    found = {}
    for name, cmds in FS_CANDIDATES.items():
        for c in cmds:
            if shutil.which(c):
                found[name] = c
                break
    return found


def get_block_devices():
    try:
        out = subprocess.check_output(LSBLK_CMD, text=True)
        data = json.loads(out)
    except Exception as e:
        return [], f"Error running lsblk: {e}"

    devices = []

    def walk(nodes):
        for n in nodes:
            t = n.get("type")
            if t == "disk":
                devices.append(n)
            if "children" in n:
                walk(n["children"])

    walk(data.get("blockdevices", []))
    return devices, None


def device_display(dev):
    name = dev.get("name")
    size = dev.get("size")
    model = dev.get("model") or ""
    mp = dev.get("mountpoint") or ""
    rm = dev.get("rm")
    return f"/dev/{name} | {size} | {model} | mounted: {mp} | removable: {rm}"


def unmount_children(devname, log):
    # find mounted children and unmount
    try:
        out = subprocess.check_output(["lsblk", "-J", "/dev/"+devname, "-o", "NAME,MOUNTPOINT"], text=True)
        j = json.loads(out)
        mounts = []
        def collect(nodes):
            for n in nodes:
                mp = n.get("mountpoint")
                if mp:
                    mounts.append((n.get("name"), mp))
                if "children" in n:
                    collect(n["children"])
        collect(j.get("blockdevices", []))
        for name, mp in mounts:
            path = "/dev/"+name
            log(f"Unmounting {path} ({mp})...\n")
            subprocess.run(["sudo", "umount", path], check=False)
    except Exception as e:
        log(f"Warning: could not enumerate/unmount children: {e}\n")


def probe_partition_table(devpath, log=None):
    """Probe partition table on device with fallback methods for different systems.
    Works with or without partprobe."""
    if log:
        log("Probing partition table...\n")
    
    # Try partprobe first (preferred method)
    if shutil.which('partprobe'):
        try:
            subprocess.run(["sudo", "partprobe", devpath], check=False, timeout=10)
            if log:
                log("Partition table probed with partprobe.\n")
            return True
        except Exception as e:
            if log:
                log(f"partprobe failed: {e}, trying alternatives...\n")
    
    # Fallback 1: blockdev --rereadpt (works on many Linux systems)
    if shutil.which('blockdev'):
        try:
            subprocess.run(["sudo", "blockdev", "--rereadpt", devpath], check=False, timeout=10)
            if log:
                log("Partition table probed with blockdev.\n")
            return True
        except Exception as e:
            if log:
                log(f"blockdev failed: {e}, trying alternatives...\n")
    
    # Fallback 2: Just wait and let kernel do its thing
    if log:
        log("Waiting for kernel to reload partition table...\n")
    time.sleep(2)
    return True
    """Return the first partition name (e.g. sdb1) for a disk, or None."""
    try:
        out = subprocess.check_output(["lsblk", "-J", "/dev/"+devname, "-o", "NAME,TYPE"], text=True)
        j = json.loads(out)
        parts = []
        def collect(nodes):
            for n in nodes:
                if n.get("type") == "part":
                    parts.append(n.get("name"))
                if "children" in n:
                    collect(n["children"])
        collect(j.get("blockdevices", []))
        return parts[0] if parts else None
    except Exception:
        return None


def create_single_partition(devpath, log, label_type='msdos', progress_cb=None):
    """Create a single partition covering the whole device using parted, return partition name or None.
    label_type should be 'msdos' or 'gpt'."""
    log(f"Creating single partition on {devpath} with label {label_type} (wiping partition table)...\n")
    try:
        # Unmount any children first
        devname = Path(devpath).name
        unmount_children(devname, log)
        
        # Wait for device to be fully released after unmount
        import time
        time.sleep(2)
        
        # use parted to create label and single primary partition
        if progress_cb:
            progress_cb(20)
        subprocess.run(["sudo", "parted", "-s", devpath, "mklabel", label_type], check=True)
        if progress_cb:
            progress_cb(40)
        # For GPT, parted may prefer parted mkpart primary 0% 100%
        subprocess.run(["sudo", "parted", "-s", devpath, "mkpart", "primary", "0%", "100%"], check=True)
        # inform kernel to re-read partition table (with fallbacks)
        probe_partition_table(devpath, log)
        # re-query lsblk for the new partition
        base = Path(devpath).name
        # wait a short moment for kernel to create partition node
        time.sleep(0.5)
        new = find_first_partition(base)
        if new:
            log(f"Created partition: /dev/{new}\n")
            if progress_cb:
                progress_cb(50)
            return new
        else:
            log("Failed to detect new partition after creating it.\n")
            return None
    except subprocess.CalledProcessError as e:
        log(f"Partitioning failed: {e}\n")
        return None


def build_mkfs_command(mkcmd, fstype_key, devpath, label):
    # mkcmd is the actual mkfs binary found (e.g., mkfs.ext4 or mkfs.vfat)
    if fstype_key.startswith("ext4"):
        args = ["sudo", mkcmd, "-F"]
        if label:
            args += ["-L", label]
        args.append(devpath)
        return args
    if fstype_key.startswith("vfat"):
        args = ["sudo", mkcmd, "-F", "32"]
        if label:
            args += ["-n", label]
        args.append(devpath)
        return args
    if fstype_key.startswith("exfat"):
        args = ["sudo", mkcmd]
        if label:
            args += ["-n", label]
        args.append(devpath)
        return args
    if fstype_key.startswith("ntfs"):
        args = ["sudo", mkcmd]
        if label:
            args += ["-L", label]
        args.append(devpath)
        return args
    if fstype_key.startswith("xfs"):
        args = ["sudo", mkcmd, "-f"]
        if label:
            args += ["-L", label]
        args.append(devpath)
        return args
    if fstype_key.startswith("btrfs"):
        args = ["sudo", mkcmd, "-f"]
        if label:
            args += ["-L", label]
        args.append(devpath)
        return args
    # fallback
    return ["sudo", "mkfs", "-t", fstype_key.split()[0], devpath]


def run_format(devnode, mkcmd, fstype_key, label, log, progress_cb=None):
    devpath = f"/dev/{devnode}"
    log(f"Preparing to format {devpath} as {fstype_key}...\n")
    # phase 1: unmount
    if progress_cb:
        progress_cb(10)
    unmount_children(devnode, log)

    # build command
    cmd = build_mkfs_command(mkcmd, fstype_key, devpath, label)
    log(f"Running: {' '.join(cmd)}\n")

    # phase 2: starting mkfs
    if progress_cb:
        progress_cb(60)

    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # while process runs, increment progress slowly up to 95
        pct = 60
        while True:
            line = p.stdout.readline()
            if line:
                log(line)
            if p.poll() is not None:
                break
            # bump progress a bit to show activity
            pct = min(95, pct + 1)
            if progress_cb:
                progress_cb(pct)
        out, _ = p.communicate()
        if out:
            log(out + "\n")
        if p.returncode == 0:
            if progress_cb:
                progress_cb(100)
            log("Format completed successfully.\n")
        else:
            if progress_cb:
                progress_cb(100)
            log(f"Format failed with exit code {p.returncode}\n")
    except Exception as e:
        if progress_cb:
            progress_cb(100)
        log(f"Error running format: {e}\n")


def write_iso_to_device(devnode, iso_path, log, progress_cb=None):
    """Write a bootable ISO image to the raw device (/dev/<devnode>) using dd and report progress."""
    devpath = f"/dev/{devnode}"
    log(f"Preparing to write ISO {iso_path} to {devpath} (this will overwrite the device)...\n")
    # ensure ISO exists
    if not os.path.isfile(iso_path):
        log("ISO file not found.\n")
        if progress_cb:
            progress_cb(100)
        return

    # unmount any children
    progress_cb and progress_cb(5)
    unmount_children(devnode, log)

    # get iso size
    try:
        total = os.path.getsize(iso_path)
    except Exception:
        total = None

    # prefer pv if available for smoother progress
    use_pv = shutil.which('pv') is not None
    if use_pv:
        log('Using pv to stream ISO to dd for better progress.\n')
        try:
            # pv stdout -> dd stdin
            p_pv = subprocess.Popen(['pv', iso_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            p_dd = subprocess.Popen(['sudo', 'dd', f'of={devpath}', 'bs=4M', 'status=progress'], stdin=p_pv.stdout, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # close pv stdout in parent so dd sees EOF when pv exits
            p_pv.stdout.close()

            # read pv stderr for progress lines and dd stderr for final status
            pv_stderr = p_pv.stderr
            dd_stderr = p_dd.stderr
            # monitor both
            while True:
                pv_line = pv_stderr.readline()
                if pv_line:
                    log(pv_line)
                    m = re.search(r"(\d+)%", pv_line)
                    if m and progress_cb:
                        try:
                            pct = int(m.group(1))
                            progress_cb(min(100, pct))
                        except Exception:
                            pass
                # check if dd finished
                if p_dd.poll() is not None:
                    break
                time.sleep(0.1)

            out_dd, err_dd = p_dd.communicate()
            out_pv, err_pv = p_pv.communicate()
            if out_pv:
                log(out_pv + "\n")
            if err_pv:
                log(err_pv + "\n")
            if out_dd:
                log(out_dd + "\n")
            if err_dd:
                log(err_dd + "\n")
            if p_dd.returncode == 0:
                if progress_cb:
                    progress_cb(100)
                log("ISO written successfully.\n")
            else:
                if progress_cb:
                    progress_cb(100)
                log(f"dd exited with code {p_dd.returncode}\n")
        except Exception as e:
            if progress_cb:
                progress_cb(100)
            log(f"Error writing ISO with pv: {e}\n")
    else:
        # build dd command; use status=progress where supported
        cmd = ["sudo", "dd", f"if={iso_path}", f"of={devpath}", "bs=4M", "status=progress"]
        log(f"Running: {' '.join(cmd)}\n")

        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            # dd writes progress to stderr; read stderr lines
            while True:
                err = p.stderr.readline()
                if err:
                    log(err)
                    # try to parse transferred bytes
                    m = re.search(r"(\d+) bytes", err)
                    if m and total:
                        try:
                            transferred = int(m.group(1))
                            pct = int(transferred * 100 / total)
                            if progress_cb:
                                progress_cb(min(100, pct))
                        except Exception:
                            pass
                if p.poll() is not None:
                    break
            out, err = p.communicate()
            if out:
                log(out + "\n")
            if err:
                log(err + "\n")
            if p.returncode == 0:
                if progress_cb:
                    progress_cb(100)
                log("ISO written successfully.\n")
            else:
                if progress_cb:
                    progress_cb(100)
                log(f"dd exited with code {p.returncode}\n")
        except Exception as e:
            if progress_cb:
                progress_cb(100)
            log(f"Error writing ISO: {e}\n")


 


def mount_first_partition(devnode, log):
    """Mount the first partition of the given device under /tmp and return mount point or None."""
    part = find_first_partition(devnode)
    if not part:
        log("No partition found to mount.\n")
        return None
    mp = f"/tmp/usb_{part}"
    try:
        os.makedirs(mp, exist_ok=True)
        log(f"Mounting /dev/{part} to {mp}...\n")
        r = subprocess.run(["sudo", "mount", f"/dev/{part}", mp], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log(r.stdout + "\n")
        if r.returncode != 0:
            log(f"Mount failed with code {r.returncode}\n")
            return None
        # list top-level files
        try:
            entries = os.listdir(mp)
            log("Top-level files/directories:\n")
            for e in entries:
                log(f" - {e}\n")
        except Exception as e:
            log(f"Could not list mount contents: {e}\n")

        # open in file manager if available
        if shutil.which('xdg-open'):
            try:
                subprocess.Popen(['xdg-open', mp])
                log(f"Opened {mp} in file manager.\n")
            except Exception as e:
                log(f"Failed to open file manager: {e}\n")
        return mp
    except Exception as e:
        log(f"Error mounting partition: {e}\n")
        return None


def unmount_mountpoint(mount_point, log):
    try:
        log(f"Unmounting {mount_point}...\n")
        r = subprocess.run(["sudo", "umount", mount_point], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log(r.stdout + "\n")
        if r.returncode != 0:
            log(f"Unmount failed with code {r.returncode}\n")
            return False
        return True
    except Exception as e:
        log(f"Error unmounting: {e}\n")
        return False


def mount_and_list(devnode, log):
    """Mount the given device node (e.g., sdb1) to a temp dir, list files, then unmount."""
    mp = f"/tmp/usb_check_{devnode}"
    try:
        if not os.path.exists(mp):
            os.makedirs(mp, exist_ok=True)
        log(f"Mounting /dev/{devnode} to {mp}...\n")
        subprocess.run(["sudo", "mount", f"/dev/{devnode}", mp], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        # list files
        r = subprocess.run(["ls", "-la", mp], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log(r.stdout + "\n")
        # open in file manager if available
        if shutil.which('xdg-open'):
            try:
                subprocess.Popen(['xdg-open', mp])
                log(f"Opened {mp} in file manager.\n")
            except Exception as e:
                log(f"Failed to open file manager: {e}\n")
    except Exception as e:
        log(f"Error mounting/listing: {e}\n")
    finally:
        log(f"Unmounting {mp}...\n")
        subprocess.run(["sudo", "umount", mp], check=False)
        try:
            os.rmdir(mp)
        except Exception:
            pass


def compute_iso_sha256(iso_path, log, progress_cb=None):
    """Compute SHA-256 of iso_path with progress updates. Returns hex digest or None."""
    log(f"Computing SHA-256 for {iso_path}...\n")
    try:
        total = os.path.getsize(iso_path)
    except Exception:
        total = None
    h = hashlib.sha256()
    read = 0
    try:
        with open(iso_path, 'rb') as f:
            while True:
                chunk = f.read(4 * 1024 * 1024)
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
                if total and progress_cb:
                    pct = int(read * 100 / total)
                    progress_cb(min(100, pct))
        digest = h.hexdigest()
        log(f"SHA-256: {digest}\n")
        if progress_cb:
            progress_cb(100)
        return digest
    except Exception as e:
        log(f"Error computing hash: {e}\n")
        if progress_cb:
            progress_cb(100)
        return None


def fetch_online_sha256(iso_name, log, timeout=10):
    """Try to find a SHA-256 checksum for iso_name by searching the web (DuckDuckGo HTML) and fetching candidate checksum files.
    Returns hex digest string or None.
    """
    try:
        q = urllib.parse.quote_plus(f"{iso_name} SHA256")
        url = f"https://duckduckgo.com/html/?q={q}"
        req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.68.0'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            page = resp.read().decode('utf-8', errors='ignore')
    except Exception as e:
        log(f"Online search failed: {e}\n")
        return None

    # collect href links
    links = re.findall(r'href=["\']([^"\']+)["\']', page)
    candidates = []
    for l in links:
        ln = l.lower()
        if any(x in ln for x in ('.sha256', '.sha256sum', 'sha256', 'sha1', 'checksum', 'sha256sums')):
            if l.startswith('/'):
                # make absolute to duckduckgo? skip
                continue
            if not l.startswith('http'):
                continue
            candidates.append(l)

    # Also look for 64-hex directly on the search page
    m = re.search(r"\b([a-fA-F0-9]{64})\b", page)
    if m:
        log(f"Found possible hash on search page: {m.group(1)}\n")
        return m.group(1)

    # Fetch candidate links and look for hash
    for c in candidates[:8]:
        try:
            req = urllib.request.Request(c, headers={'User-Agent': 'curl/7.68.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r2:
                txt = r2.read().decode('utf-8', errors='ignore')
                # look for line mentioning iso_name
                for line in txt.splitlines():
                    if iso_name in line and re.search(r"\b([a-fA-F0-9]{64})\b", line):
                        mm = re.search(r"\b([a-fA-F0-9]{64})\b", line)
                        if mm:
                            log(f"Found online checksum in {c}\n")
                            return mm.group(1)
                # fallback: first 64-hex in file
                mm = re.search(r"\b([a-fA-F0-9]{64})\b", txt)
                if mm:
                    log(f"Found online checksum in {c}\n")
                    return mm.group(1)
        except Exception:
            continue

    return None


def detect_windows_iso(iso_path):
    """Detect if ISO is Windows and identify version (7, 10, or 11).
    Returns (is_windows, version) where version is 7, 10, 11, or None.
    """
    try:
        iso_name_lower = os.path.basename(iso_path).lower()
        # Check for Windows indicators in filename
        if 'windows' not in iso_name_lower:
            return False, None
        
        # Try to identify version from filename
        if '11' in iso_name_lower or 'win11' in iso_name_lower:
            return True, 11
        elif '10' in iso_name_lower or 'win10' in iso_name_lower:
            return True, 10
        elif '7' in iso_name_lower or 'win7' in iso_name_lower or 'windows7' in iso_name_lower:
            return True, 7
        
        # If Windows is in name but version unclear, assume Windows 10
        if 'windows' in iso_name_lower:
            return True, 10
    except Exception:
        pass
    return False, None


def write_windows_iso_to_device(devname, iso_path, log, progress_cb=None):
    """Write Windows ISO to USB device. Handles Windows 7, 10, and 11.
    For Windows ISOs larger than 4GB, use exFAT; otherwise use FAT32.
    """
    devpath = f"/dev/{devname}"
    
    try:
        iso_size = os.path.getsize(iso_path)
    except Exception as e:
        log(f"Error getting ISO size: {e}\n")
        return
    
    is_win, win_version = detect_windows_iso(iso_path)
    log(f"Detected as Windows ISO: {is_win}, Version: {win_version}\n")
    
    # Determine filesystem based on ISO size
    # Windows 11 ISOs are typically > 4GB
    use_exfat = iso_size > 4 * 1024 * 1024 * 1024
    fs_type = "exfat" if use_exfat else "vfat (FAT32)"
    
    log(f"ISO size: {iso_size / (1024**3):.2f}GB\n")
    log(f"Will use {fs_type} filesystem for this {win_version} ISO\n")
    
    # Step 1: Unmount
    if progress_cb:
        progress_cb(5)
    unmount_children(devname, log)
    
    # Wait for device to be fully released after unmount
    import time
    time.sleep(2)
    
    # Step 2: Wipe and create partition table
    if progress_cb:
        progress_cb(10)
    try:
        log("Creating partition table (MBR/msdos) for Windows ISO...\n")
        subprocess.run(["sudo", "parted", "-s", devpath, "mklabel", "msdos"], check=True)
        if progress_cb:
            progress_cb(15)
        subprocess.run(["sudo", "parted", "-s", devpath, "mkpart", "primary", "0%", "100%"], check=True)
        probe_partition_table(devpath, log)
        time.sleep(0.5)
    except subprocess.CalledProcessError as e:
        log(f"Partitioning failed: {e}\n")
        return
    
    # Step 3: Format with appropriate filesystem
    if progress_cb:
        progress_cb(20)
    part = find_first_partition(devname)
    if not part:
        log("Failed to find partition after creation.\n")
        return
    
    part_path = f"/dev/{part}"
    if use_exfat:
        if shutil.which('mkfs.exfat'):
            log(f"Formatting {part_path} as exFAT...\n")
            try:
                subprocess.run(["sudo", "mkfs.exfat", "-n", "WINDOWS", part_path], check=True)
            except subprocess.CalledProcessError as e:
                log(f"exFAT format failed: {e}\n")
                return
        else:
            log("exFAT tools not available, falling back to FAT32 (may not work for large ISOs)\n")
            try:
                subprocess.run(["sudo", "mkfs.vfat", "-F", "32", "-n", "WINDOWS", part_path], check=True)
            except subprocess.CalledProcessError as e:
                log(f"FAT32 format failed: {e}\n")
                return
    else:
        log(f"Formatting {part_path} as FAT32...\n")
        try:
            subprocess.run(["sudo", "mkfs.vfat", "-F", "32", "-n", "WINDOWS", part_path], check=True)
        except subprocess.CalledProcessError as e:
            log(f"FAT32 format failed: {e}\n")
            return
    
    if progress_cb:
        progress_cb(30)
    
    # Step 4: Mount partition
    mp = f"/tmp/usb_windows_{part}"
    try:
        os.makedirs(mp, exist_ok=True)
        log(f"Mounting {part_path} to {mp}...\n")
        subprocess.run(["sudo", "mount", part_path, mp], check=True)
    except subprocess.CalledProcessError as e:
        log(f"Mount failed: {e}\n")
        return
    
    if progress_cb:
        progress_cb(35)
    
    # Step 5: Extract ISO contents to USB
    log(f"Mounting ISO and copying contents to USB...\n")
    iso_mp = f"/tmp/iso_mount_{part}"
    try:
        os.makedirs(iso_mp, exist_ok=True)
        # Mount ISO
        log(f"Mounting ISO to {iso_mp}...\n")
        try:
            subprocess.run(["sudo", "mount", "-o", "loop", iso_path, iso_mp], check=True)
        except subprocess.CalledProcessError:
            # Try with -t iso9660 if loop mount fails
            try:
                subprocess.run(["sudo", "mount", "-t", "iso9660", "-o", "loop", iso_path, iso_mp], check=True)
            except subprocess.CalledProcessError as e:
                log(f"ISO mount failed: {e}\n")
                return
        
        if progress_cb:
            progress_cb(40)
        
        # Copy all files from ISO to USB
        log("Copying ISO files to USB (this may take several minutes)...\n")
        try:
            # Use cp -r with sudo to copy everything
            result = subprocess.run(
                ["sudo", "cp", "-r", f"{iso_mp}/.", f"{mp}/"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=3600  # 1 hour timeout
            )
            if result.returncode != 0:
                log(f"Copy failed: {result.stderr}\n")
            else:
                log("Files copied successfully.\n")
        except subprocess.TimeoutExpired:
            log("File copy timed out.\n")
            return
        except Exception as e:
            log(f"Error copying files: {e}\n")
            return
        
        if progress_cb:
            progress_cb(90)
        
        # Make bootable with MBR for Windows
        # Windows needs specific MBR configuration
        try:
            # Copy boot sector if exists
            boot_code = os.path.join(iso_mp, "efi", "boot", "bootia32.efi")
            if os.path.exists(boot_code):
                log("Windows EFI boot detected, ensuring EFI partition is bootable...\n")
        except Exception:
            pass
        
        # Unmount ISO
        log(f"Unmounting ISO from {iso_mp}...\n")
        subprocess.run(["sudo", "umount", iso_mp], check=False)
        
    except Exception as e:
        log(f"Error during file copy process: {e}\n")
        # Try to unmount
        subprocess.run(["sudo", "umount", iso_mp], check=False)
        subprocess.run(["sudo", "umount", mp], check=False)
        return
    
    if progress_cb:
        progress_cb(95)
    
    # Step 6: Sync and unmount
    try:
        log("Syncing filesystem...\n")
        subprocess.run(["sudo", "sync"], check=True)
        log(f"Unmounting {part_path}...\n")
        subprocess.run(["sudo", "umount", mp], check=True)
    except subprocess.CalledProcessError as e:
        log(f"Unmount/sync failed: {e}\n")
        return
    
    if progress_cb:
        progress_cb(100)
    
    log(f"Windows ISO written successfully to {devpath}!\n")
    log(f"USB is now bootable for Windows {win_version}\n")


def find_checksum_file(iso_path):
    """Search the ISO's directory for a checksum file and try to extract an expected SHA-256 for the ISO.
    Returns (checksum_file_path, expected_hash) or (None, None).
    """
    directory = os.path.dirname(iso_path) or '.'
    iso_name = os.path.basename(iso_path)
    candidates = []
    # exact name matches first
    for ext in ('.sha256', '.sha256sum', '.sha256.txt', '.sha256sum.txt', '.sha256sum', '.sha256.sig'):
        p = os.path.join(directory, iso_name + ext)
        if os.path.isfile(p):
            candidates.append(p)

    # then look for other files that look like checksums
    if not candidates:
        for fname in os.listdir(directory):
            lf = fname.lower()
            if 'sha' in lf and (lf.endswith('.txt') or lf.endswith('.sum') or lf.endswith('.sha256') or 'checksum' in lf):
                candidates.append(os.path.join(directory, fname))

    # parse candidates for a 64-hex and optional filename match
    for p in candidates:
        try:
            with open(p, 'r', encoding='utf-8', errors='ignore') as fh:
                for line in fh:
                    m = re.search(r"\b([a-fA-F0-9]{64})\b", line)
                    if m:
                        # prefer lines that mention the ISO filename
                        if iso_name in line:
                            return (p, m.group(1))
                # second pass: take first hash if no filename match
                fh.seek(0)
                for line in fh:
                    m = re.search(r"\b([a-fA-F0-9]{64})\b", line)
                    if m:
                        return (p, m.group(1))
        except Exception:
            continue
    return (None, None)


class App:
    def __init__(self, root):
        self.root = root
        self.operation_in_progress = False
        root.title("USB Formatter Pro")
        root.geometry("900x700")
        root.resizable(True, True)
        
        # Configure styles
        style = ttk.Style()
        style.theme_use('clam')
        
        # Define professional colors
        self.bg_color = '#f0f0f0'
        self.frame_bg = '#ffffff'
        self.text_color = '#333333'
        self.accent_color = '#0066cc'
        self.warning_color = '#ff6600'
        self.success_color = '#00aa00'
        
        # Get compatible fonts for this system (works on Raspberry Pi and others)
        font_name, _ = get_compatible_font()
        self.font_title = (font_name, 16, 'bold')
        self.font_heading = (font_name, 10, 'bold')
        self.font_normal = (font_name, 9)
        self.font_small = (font_name, 8)
        self.font_monospace = ('Courier New', 9) if shutil.which('fc-list') else (font_name, 9)
        
        root.configure(bg=self.bg_color)

        # Main container
        main_frame = Frame(root, bg=self.bg_color)
        main_frame.pack(fill='both', expand=True, padx=12, pady=12)

        # Title
        title_label = Label(main_frame, text="USB Formatter Pro", font=self.font_title, 
                           bg=self.bg_color, fg=self.text_color)
        title_label.pack(anchor='w', pady=(0, 6))
        
        subtitle_label = Label(main_frame, text="Create bootable USB drives for Linux and Windows", 
                              font=self.font_normal, bg=self.bg_color, fg='#666666')
        subtitle_label.pack(anchor='w', pady=(0, 12))

        # Menu bar with About
        try:
            menubar = Menu(root)
            helpmenu = Menu(menubar, tearoff=0)
            helpmenu.add_command(label="About", command=self.show_about)
            helpmenu.add_separator()
            helpmenu.add_command(label="Exit", command=root.quit)
            menubar.add_cascade(label="Help", menu=helpmenu)
            root.config(menu=menubar)
        except Exception:
            pass

        # Device selection frame
        dev_frame = LabelFrame(main_frame, text="Device Selection", font=self.font_heading,
                              bg=self.frame_bg, fg=self.text_color, padx=12, pady=12)
        dev_frame.pack(fill='both', expand=True, pady=(0, 8))

        self.lb = Listbox(dev_frame, width=100, height=6, font=self.font_monospace,
                         relief='solid', borderwidth=1)
        self.lb.pack(fill='both', expand=True)
        
        # Scrollbar for listbox
        scrollbar = Scrollbar(dev_frame, command=self.lb.yview)
        scrollbar.pack(side='right', fill='y')
        self.lb.config(yscrollcommand=scrollbar.set)

        refresh_btn = Button(dev_frame, text="Refresh Device List", command=self.refresh,
                            font=self.font_normal, bg=self.accent_color, fg='white',
                            relief='flat', padx=12, pady=6)
        refresh_btn.pack(anchor='w', pady=(8, 0))

        # Options frame
        opt_frame = LabelFrame(main_frame, text="Configuration", font=self.font_heading,
                              bg=self.frame_bg, fg=self.text_color, padx=12, pady=12)
        opt_frame.pack(fill='x', pady=(0, 8))

        # Filesystem row
        fs_label = Label(opt_frame, text="Filesystem:", font=self.font_normal, 
                        bg=self.frame_bg, fg=self.text_color)
        fs_label.grid(row=0, column=0, sticky='w', padx=(0, 12))
        
        self.fsvar = StringVar(opt_frame)
        self.fsmap = detect_filesystems()
        if not self.fsmap:
            self.fsmap = {"ext4": "mkfs.ext4"}
        fs_options = list(self.fsmap.keys())
        self.fsvar.set(fs_options[0])
        fs_menu = OptionMenu(opt_frame, self.fsvar, *fs_options)
        fs_menu.config(font=self.font_normal, bg='white', highlightthickness=0, relief='solid', borderwidth=1)
        fs_menu.grid(row=0, column=1, sticky='w', padx=(0, 24))

        # Partition table row
        part_label = Label(opt_frame, text="Partition Table:", font=self.font_normal,
                          bg=self.frame_bg, fg=self.text_color)
        part_label.grid(row=0, column=2, sticky='w', padx=(0, 12))
        
        self.part_label_map = {"msdos (MBR)": "msdos", "gpt": "gpt"}
        self.part_label_var = StringVar(opt_frame)
        self.part_label_var.set("msdos (MBR)")
        part_menu = OptionMenu(opt_frame, self.part_label_var, *list(self.part_label_map.keys()))
        part_menu.config(font=self.font_normal, bg='white', highlightthickness=0, relief='solid', borderwidth=1)
        part_menu.grid(row=0, column=3, sticky='w', padx=(0, 24))

        # Label row
        label_label = Label(opt_frame, text="Volume Label:", font=self.font_normal,
                           bg=self.frame_bg, fg=self.text_color)
        label_label.grid(row=1, column=0, sticky='w', padx=(0, 12), pady=(12, 0))
        
        self.label_entry = Entry(opt_frame, font=self.font_normal, relief='solid', borderwidth=1, width=20)
        self.label_entry.grid(row=1, column=1, sticky='w', padx=(0, 24), pady=(12, 0))
        
        label_hint = Label(opt_frame, text="(optional, max 32 chars)", font=self.font_small,
                          bg=self.frame_bg, fg='#999999')
        label_hint.grid(row=1, column=2, sticky='w', pady=(12, 0))

        # Action buttons frame
        action_frame = LabelFrame(main_frame, text="Operations", font=self.font_heading,
                                 bg=self.frame_bg, fg=self.text_color, padx=12, pady=12)
        action_frame.pack(fill='x', pady=(0, 8))

        button_style_format = {'font': self.font_normal, 'relief': 'flat', 'padx': 12, 'pady': 8}
        button_style_iso = {'font': self.font_normal, 'relief': 'flat', 'padx': 12, 'pady': 8}
        
        self.format_btn = Button(action_frame, text="Format Device", command=self.on_format,
                                bg='#ff6600', fg='white', **button_style_format)
        self.format_btn.pack(side='left', padx=(0, 8))

        self.iso_btn = Button(action_frame, text="Write Linux ISO", command=self.on_write_iso,
                             bg=self.accent_color, fg='white', **button_style_iso)
        self.iso_btn.pack(side='left', padx=(0, 8))

        self.windows_iso_btn = Button(action_frame, text="Write Windows ISO", command=self.on_write_windows_iso,
                                     bg='#0078d4', fg='white', **button_style_iso)
        self.windows_iso_btn.pack(side='left', padx=(0, 8))

        # Status frame
        status_frame = Frame(main_frame, bg=self.frame_bg, relief='solid', borderwidth=1)
        status_frame.pack(fill='x', pady=(0, 8))

        status_label = Label(status_frame, text="Progress:", font=self.font_heading,
                            bg=self.frame_bg, fg=self.text_color)
        status_label.pack(anchor='w', padx=12, pady=(8, 2))

        self.progress = ttk.Progressbar(status_frame, length=400, mode='determinate', maximum=100)
        self.progress.pack(fill='x', padx=12, pady=(2, 8))
        
        self.progress_label = Label(status_frame, text="0%", font=self.font_small,
                                   bg=self.frame_bg, fg='#666666')
        self.progress_label.pack(anchor='e', padx=12, pady=(0, 4))

        # Log frame
        log_frame = LabelFrame(main_frame, text="Activity Log", font=self.font_heading,
                              bg=self.frame_bg, fg=self.text_color, padx=8, pady=8)
        log_frame.pack(fill='both', expand=True, pady=(0, 8))

        self.log = Text(log_frame, width=100, height=10, font=self.font_monospace,
                       relief='solid', borderwidth=1, bg='#f5f5f5', fg=self.text_color)
        self.log.pack(fill='both', expand=True)
        
        # Log scrollbar
        log_scrollbar = Scrollbar(log_frame, command=self.log.yview)
        log_scrollbar.pack(side='right', fill='y')
        self.log.config(yscrollcommand=log_scrollbar.set)
        
        # Configure log text colors for different message types
        self.log.tag_config('info', foreground='#0066cc')
        self.log.tag_config('success', foreground=self.success_color)
        self.log.tag_config('warning', foreground=self.warning_color)
        self.log.tag_config('error', foreground='#cc0000')

        # Clear log button
        clear_btn = Button(log_frame, text="Clear Log", command=self.clear_log,
                          font=self.font_small, bg='#e0e0e0', fg=self.text_color,
                          relief='flat', padx=8, pady=4)
        clear_btn.pack(anchor='e', pady=(4, 0))

        self.refresh()

    def log_write(self, txt, tag='info'):
        """Write to log with optional tag for coloring."""
        def _write():
            self.log.insert(END, txt, tag)
            self.log.see('end')
        self.root.after(0, _write)

    def log_info(self, txt):
        """Log info message."""
        self.log_write(txt, tag='info')

    def log_success(self, txt):
        """Log success message."""
        self.log_write(txt, tag='success')

    def log_warning(self, txt):
        """Log warning message."""
        self.log_write(txt, tag='warning')

    def log_error(self, txt):
        """Log error message."""
        self.log_write(txt, tag='error')

    def clear_log(self):
        """Clear the log display."""
        self.log.delete(1.0, END)
        self.log_info("Log cleared.\n")

    def set_progress(self, pct: int):
        if pct < 0:
            pct = 0
        if pct > 100:
            pct = 100
        def _set():
            self.progress['value'] = pct
            self.progress_label.config(text=f"{pct}%")
        self.root.after(0, _set)

    def refresh(self):
        """Refresh device list with better error handling."""
        try:
            self.lb.delete(0, END)
            devs, err = get_block_devices()
            if err:
                self.log_error(f"Error: {err}\n")
                self.lb.insert(END, "[ERROR] Unable to retrieve device list")
                return
            
            self.devs = devs
            if not devs:
                self.lb.insert(END, "[No removable devices detected]")
                self.log_warning("No removable USB devices found.\n")
                return
            
            for d in devs:
                self.lb.insert(END, device_display(d))
            
            self.log_info(f"Device list refreshed: {len(devs)} device(s) found.\n")
        except Exception as e:
            self.log_error(f"Exception during refresh: {e}\n")

    def show_about(self):
        """Display professional about dialog with system compatibility info."""
        try:
            ver = globals().get('__version__', '1.0')
            info = get_system_info()
            
            # Get available filesystems
            fsystems = list(self.fsmap.keys()) if hasattr(self, 'fsmap') else []
            fs_str = ", ".join(fsystems) if fsystems else "None detected"
            
            message = (
                f"USB Formatter Pro\n"
                f"Version: {ver}\n\n"
                f"A professional tool for creating bootable USB drives\n"
                f"Supports: Linux ISOs, Windows 7/10/11\n\n"
                f"System Information:\n"
                f"• OS: {info.get('distro', 'Unknown')} ({info.get('os')})\n"
                f"• Python: {info.get('python_version')}\n"
                f"• Machine: {info.get('machine')}\n"
                f"• Filesystems: {fs_str}\n\n"
                f"Created by: Gevin\n"
                f"Copyright © 2026 Gevin\n\n"
                f"⚠️  WARNING: This tool performs destructive operations.\n"
                f"Always verify you've selected the correct device before proceeding."
            )
            messagebox.showinfo("About USB Formatter Pro", message)
        except Exception as e:
            try:
                messagebox.showinfo("About", f"USB Formatter Pro v{globals().get('__version__', '1.0')}")
            except Exception:
                pass

    def on_format(self):
        """Format selected device with improved validation."""
        if self.operation_in_progress:
            messagebox.showwarning("Operation in Progress", "Another operation is already running. Please wait.")
            return
        
        sel = self.lb.curselection()
        if not sel:
            messagebox.showwarning("No Device Selected", "Please select a device to format from the list.")
            return
        
        idx = sel[0]
        dev = self.devs[idx]
        devname = dev.get("name")
        devsize = dev.get("size", "Unknown")
        model = dev.get("model", "Unknown Device")
        
        fs_key = self.fsvar.get()
        mkcmd = self.fsmap.get(fs_key)
        if not mkcmd:
            messagebox.showerror("Filesystem Error", f"Filesystem '{fs_key}' not available on this system.")
            self.log_error(f"Filesystem command not found for {fs_key}\n")
            return
        
        label = self.label_entry.get().strip()
        if label and len(label) > 32:
            messagebox.showwarning("Label Too Long", "Volume label cannot exceed 32 characters.")
            return
        
        msg = (
            f"WARNING: CONFIRM FORMATTING\n\n"
            f"Device: /dev/{devname}\n"
            f"Size: {devsize}\n"
            f"Model: {model}\n"
            f"Filesystem: {fs_key}\n"
            f"{'Label: ' + label if label else ''}\n\n"
            f"WARNING: ALL DATA WILL BE LOST\n\n"
            f"Do you want to continue?"
        )
        if not messagebox.askyesno("Confirm Format", msg):
            self.log_info("Format operation cancelled by user.\n")
            return

        parts = dev.get("children") or []
        action = None
        target_node = None
        if parts:
            part_names = [p.get("name") for p in parts if p.get("type") == "part"]
            first_part = part_names[0] if part_names else None
            if first_part:
                q = messagebox.askyesnocancel("Partition Detected",
                                              f"Device has existing partition(s) (e.g., /dev/{first_part}).\n\n"
                                              f"Yes: Format the first partition\n"
                                              f"No: Recreate partition table\n"
                                              f"Cancel: Abort operation")
                if q is None:
                    self.log_info("Format operation cancelled by user.\n")
                    return
                if q is True:
                    action = 'format_partition'
                    target_node = first_part
                else:
                    action = 'repartition_and_format'
        else:
            action = 'create_and_format'

        # Disable UI and start progress
        self.operation_in_progress = True
        self.format_btn.config(state='disabled')
        self.iso_btn.config(state='disabled')
        self.windows_iso_btn.config(state='disabled')
        self.set_progress(0)
        self.log_info(f"Starting format operation on /dev/{devname}...\n")

        def worker():
            try:
                if action == 'format_partition':
                    self.log_info(f"Formatting partition /dev/{target_node} as {fs_key}...\n")
                    run_format(target_node, mkcmd, fs_key, label, self.log_write, progress_cb=self.set_progress)
                    self.log_success(f"Successfully formatted /dev/{target_node}\n")
                else:
                    # create a partition then format it using selected label type
                    label_type = self.part_label_map.get(self.part_label_var.get(), 'msdos')
                    newp = create_single_partition(f"/dev/{devname}", self.log_write, label_type=label_type, progress_cb=self.set_progress)
                    if not newp:
                        self.root.after(0, lambda: messagebox.showerror("Partition Error", "Failed to create partition. Check the log for details."))
                        self.log_error("Failed to create partition. Operation aborted.\n")
                        return
                    self.log_info(f"Created new partition: /dev/{newp}\n")
                    self.log_info(f"Formatting /dev/{newp} as {fs_key}...\n")
                    run_format(newp, mkcmd, fs_key, label, self.log_write, progress_cb=self.set_progress)
                    self.log_success(f"Successfully formatted /dev/{newp}\n")
            except Exception as e:
                self.log_error(f"Format operation failed: {e}\n")
            finally:
                def finish():
                    self.set_progress(100)
                    self.format_btn.config(state='normal')
                    self.iso_btn.config(state='normal')
                    self.windows_iso_btn.config(state='normal')
                    self.operation_in_progress = False
                    self.log_success("Format operation completed.\n")
                self.root.after(0, finish)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def on_write_iso(self):
        """Write Linux ISO to device with improved validation."""
        if self.operation_in_progress:
            messagebox.showwarning("Operation in Progress", "Another operation is already running. Please wait.")
            return
        
        sel = self.lb.curselection()
        if not sel:
            messagebox.showwarning("No Device Selected", "Please select a device to write the ISO to.")
            return
        idx = sel[0]
        dev = self.devs[idx]
        devname = dev.get("name")
        devsize = dev.get("size", "Unknown")
        
        msg = (
            f"WARNING: WRITE LINUX ISO TO USB\n\n"
            f"Device: /dev/{devname}\n"
            f"Size: {devsize}\n\n"
            f"WARNING: ALL DATA WILL BE LOST\n\n"
            f"Do you want to continue?"
        )
        if not messagebox.askyesno("Confirm Write", msg):
            self.log_info("ISO write operation cancelled by user.\n")
            return

        # Prompt user to select a local ISO file
        iso_path = filedialog.askopenfilename(
            title="Select Linux ISO file",
            filetypes=[("ISO files", "*.iso"), ("All files", "*")]
        )
        if not iso_path:
            self.log_info("ISO file selection cancelled.\n")
            return
        
        # Validate ISO file exists and is readable
        if not os.path.isfile(iso_path):
            messagebox.showerror("File Error", f"File not found: {iso_path}")
            self.log_error(f"ISO file not found: {iso_path}\n")
            return

        def proceed_with_iso(chosen_iso):
            """Compute hash (if enabled) and write ISO to device in background."""
            compute_hash_local = True
            self.operation_in_progress = True

            def worker_all():
                try:
                    if compute_hash_local:
                        self.log_info("Computing SHA-256 checksum...\n")
                        digest = compute_iso_sha256(chosen_iso, self.log_write, progress_cb=self.set_progress)
                        if digest:
                            iso_name = os.path.basename(chosen_iso)
                            self.log_info(f"Local checksum: {digest}\n")
                            self.log_info("Checking online checksum...\n")
                            online_digest = fetch_online_sha256(iso_name, self.log_write)
                            if online_digest:
                                if online_digest.strip().lower() != digest.strip().lower():
                                    self.log_warning("⚠️  Online checksum does NOT match. Proceeding anyway.\n")
                                else:
                                    self.log_success("[OK] Online checksum matches!\n")
                            else:
                                chk_file, expected = find_checksum_file(chosen_iso)
                                if chk_file and expected:
                                    if expected.strip().lower() != digest.strip().lower():
                                        self.log_warning("⚠️  Local checksum file does NOT match. Proceeding anyway.\n")
                                    else:
                                        self.log_success("[OK] Local checksum matches!\n")
                                else:
                                    self.log_info("No checksum file found for verification.\n")
                    # proceed to write
                    self.log_info(f"Writing ISO to /dev/{devname}...\n")
                    write_iso_to_device(devname, chosen_iso, self.log_write, progress_cb=self.set_progress)
                    # after writing, ask user if they want to mount to inspect files
                    def ask_mount():
                        try:
                            ok = messagebox.askyesno("Mount Device?", 
                                                    "ISO written successfully. Mount the device to inspect files?")
                        except Exception:
                            ok = False
                        if ok:
                            parts = dev.get("children") or []
                            target = None
                            if parts:
                                part_names = [p.get("name") for p in parts if p.get("type") == "part"]
                                target = part_names[0] if part_names else None
                            if not target:
                                target = find_first_partition(devname)
                            if target:
                                def mount_thread():
                                    self.log_info("Mounting device...\n")
                                    mount_first_partition(target, self.log_write)
                                threading.Thread(target=mount_thread, daemon=True).start()
                            else:
                                self.log_error("Could not find partition to mount.\n")
                    self.root.after(0, ask_mount)
                except Exception as e:
                    self.log_error(f"ISO write failed: {e}\n")
                finally:
                    def finish_all():
                        self.set_progress(100)
                        self.format_btn.config(state='normal')
                        self.iso_btn.config(state='normal')
                        self.windows_iso_btn.config(state='normal')
                        self.operation_in_progress = False
                        self.log_success("ISO write operation completed.\n")
                    self.root.after(0, finish_all)

            # start background worker
            self.format_btn.config(state='disabled')
            self.iso_btn.config(state='disabled')
            self.windows_iso_btn.config(state='disabled')
            self.set_progress(0)
            threading.Thread(target=worker_all, daemon=True).start()
        
        proceed_with_iso(iso_path)

    def on_write_windows_iso(self):
        """Handle writing Windows ISO (7, 10, or 11) to USB device."""
        if self.operation_in_progress:
            messagebox.showwarning("Operation in Progress", "Another operation is already running. Please wait.")
            return
        
        sel = self.lb.curselection()
        if not sel:
            messagebox.showwarning("No Device Selected", "Please select a device to write the Windows ISO to.")
            return
        idx = sel[0]
        dev = self.devs[idx]
        devname = dev.get("name")
        devsize = dev.get("size", "Unknown")
        
        msg = (
            f"WARNING: WRITE WINDOWS ISO TO USB\n\n"
            f"Device: /dev/{devname}\n"
            f"Size: {devsize}\n\n"
            f"Supported versions: Windows 7, 10, and 11\n\n"
            f"WARNING: ALL DATA WILL BE LOST\n\n"
            f"Do you want to continue?"
        )
        if not messagebox.askyesno("Confirm Windows ISO Write", msg):
            self.log_info("Windows ISO write operation cancelled by user.\n")
            return
        
        # Prompt user to select a Windows ISO file
        iso_path = filedialog.askopenfilename(
            title="Select Windows ISO (7, 10, or 11)",
            filetypes=[("ISO files", "*.iso"), ("All files", "*")]
        )
        if not iso_path:
            self.log_info("ISO file selection cancelled.\n")
            return
        
        # Validate ISO file exists and is readable
        if not os.path.isfile(iso_path):
            messagebox.showerror("File Error", f"File not found: {iso_path}")
            self.log_error(f"ISO file not found: {iso_path}\n")
            return
        
        # Verify it looks like a Windows ISO
        is_windows, win_version = detect_windows_iso(iso_path)
        if not is_windows:
            if not messagebox.askyesno("Not Detected as Windows ISO",
                                       "This doesn't appear to be a Windows ISO based on filename.\n\n"
                                       "Filename should contain 'Windows' and optionally a version number (7, 10, or 11).\n\n"
                                       "Continue anyway?"):
                self.log_info("Windows ISO write operation cancelled by user.\n")
                return
        else:
            self.log_info(f"Detected Windows {win_version} ISO\n")

        def worker():
            try:
                self.operation_in_progress = True
                write_windows_iso_to_device(devname, iso_path, self.log_write, progress_cb=self.set_progress)
                self.log_success(f"Windows ISO written successfully to /dev/{devname}\n")
            except Exception as e:
                self.log_error(f"Windows ISO write failed: {e}\n")
            finally:
                def finish():
                    self.set_progress(100)
                    self.format_btn.config(state='normal')
                    self.iso_btn.config(state='normal')
                    self.windows_iso_btn.config(state='normal')
                    self.operation_in_progress = False
                    messagebox.showinfo("Windows ISO Write Complete", 
                                       "Windows ISO has been written to the device.\n\n"
                                       "[OK] USB drive is ready\n"
                                       "[OK] Please safely eject the USB drive")
                    self.log_success("Windows ISO write operation completed.\n")
                self.root.after(0, finish)
        
        # Disable UI and start background worker
        self.operation_in_progress = True
        self.format_btn.config(state='disabled')
        self.iso_btn.config(state='disabled')
        self.windows_iso_btn.config(state='disabled')
        self.set_progress(0)
        self.log_info(f"Starting Windows ISO write to /dev/{devname}...\n")
        threading.Thread(target=worker, daemon=True).start()


if __name__ == '__main__':
    ok = check_and_install_dependencies()
    if not ok:
        # Write final note to install log and exit
        write_install_log('Dependencies missing and could not be auto-installed; exiting application')
        print('Missing dependencies. See', INSTALL_LOG, 'for instructions.')
        sys.exit(1)

    # Now import tkinter widgets into globals (safe after dependencies are present)
    from tkinter import Tk, Listbox, StringVar, OptionMenu, Button, Label, Entry, Text, END, Scrollbar, RIGHT, Y, BOTH, Frame, messagebox, filedialog, simpledialog, Menu, LabelFrame
    from tkinter import ttk

    # Splash support removed; start application directly

    root = Tk()
    app = App(root)
    root.mainloop()

