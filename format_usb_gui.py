#!/usr/bin/env python3
"""
Simple USB formatter GUI for Raspberry Pi OS (Tkinter).
- Lists block devices via lsblk
- Detects available mkfs tools and presents filesystem options
- Unmounts mounted partitions, runs mkfs with sudo
Note: This tool performs destructive operations. Run with care and preferably as root (sudo).
"""

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
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False
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


def check_and_install_dependencies():
    """Check required tools and try to install missing packages on Debian-based systems.
    Returns True if all dependencies satisfied (after possible install), False otherwise.
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

    # Map package -> binary to check
    pkg_map = {
        'parted': 'parted',
        'dosfstools': 'mkfs.vfat',
        'exfat-utils': 'mkfs.exfat',
        'ntfs-3g': 'mkfs.ntfs',
        'btrfs-progs': 'mkfs.btrfs',
        'xfsprogs': 'mkfs.xfs',
        'pv': 'pv',
    }

    for pkg, binname in pkg_map.items():
        if shutil.which(binname) is None:
            missing_pkgs.append(pkg)

    # Always require lsblk and dd
    for binname in ('lsblk', 'dd'):
        if shutil.which(binname) is None:
            missing_pkgs.append('coreutils' if binname == 'dd' else 'util-linux')

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

    # Try to install via apt-get
    try:
        write_install_log('Running apt-get update')
        r = subprocess.run(['sudo', 'apt-get', 'update'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        write_install_log(r.stdout)
        write_install_log('Running apt-get install')
        cmd = ['sudo', 'apt-get', 'install', '-y'] + missing_pkgs
        r2 = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        write_install_log(r2.stdout)
        if r2.returncode != 0:
            write_install_log(f'apt-get install failed with code {r2.returncode}')
            return False
    except Exception as e:
        write_install_log(f'Exception during apt install: {e}')
        return False

    # Re-check tkinter import
    try:
        import tkinter  # type: ignore
        write_install_log('tkinter import successful after install')
    except Exception as e:
        write_install_log(f'tkinter still unavailable: {e}')
        return False

    write_install_log('Dependencies installed successfully')
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
    return f"/dev/{name} — {size} — {model} — mounted: {mp} — removable: {rm}"


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


def find_first_partition(devname):
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
        # use parted to create label and single primary partition
        if progress_cb:
            progress_cb(20)
        subprocess.run(["sudo", "parted", "-s", devpath, "mklabel", label_type], check=True)
        if progress_cb:
            progress_cb(40)
        # For GPT, parted may prefer parted mkpart primary 0% 100%
        subprocess.run(["sudo", "parted", "-s", devpath, "mkpart", "primary", "0%", "100%"], check=True)
        # inform kernel to re-read partition table
        subprocess.run(["sudo", "partprobe", devpath], check=False)
        # re-query lsblk for the new partition
        base = Path(devpath).name
        # wait a short moment for kernel to create partition node
        import time; time.sleep(0.5)
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
        root.title("USB Formatter")

        frame = Frame(root)
        frame.pack(padx=8, pady=8)

        Label(frame, text="Devices:").grid(row=0, column=0, sticky='w')
        self.lb = Listbox(frame, width=80, height=6)
        self.lb.grid(row=1, column=0, columnspan=3)

        Button(frame, text="Refresh", command=self.refresh).grid(row=2, column=0, sticky='w')

        Label(frame, text="Filesystem:").grid(row=3, column=0, sticky='w')
        self.fsvar = StringVar(frame)
        self.fsmap = detect_filesystems()
        if not self.fsmap:
            self.fsmap = {"ext4": "mkfs.ext4"}
        options = list(self.fsmap.keys())
        self.fsvar.set(options[0])
        OptionMenu(frame, self.fsvar, *options).grid(row=3, column=1, sticky='w')

        Label(frame, text="Partition table:").grid(row=3, column=2, sticky='w')
        # display->value map for partition table labels
        self.part_label_map = {"msdos (MBR)": "msdos", "gpt": "gpt"}
        self.part_label_var = StringVar(frame)
        self.part_label_var.set("msdos (MBR)")
        OptionMenu(frame, self.part_label_var, *list(self.part_label_map.keys())).grid(row=3, column=3, sticky='w')

        Label(frame, text="Label (optional):").grid(row=4, column=0, sticky='w')
        self.label_entry = Entry(frame)
        self.label_entry.grid(row=4, column=1, sticky='w')

        self.format_btn = Button(frame, text="Format Selected Device", command=self.on_format)
        self.format_btn.grid(row=5, column=0, pady=6)
        self.iso_btn = Button(frame, text="Write ISO (Make Bootable)", command=self.on_write_iso)
        self.iso_btn.grid(row=5, column=1, pady=6)

        Label(frame, text="Log:").grid(row=6, column=0, sticky='w')
        self.log = Text(frame, width=80, height=12)
        self.log.grid(row=7, column=0, columnspan=3)

        self.progress = ttk.Progressbar(frame, length=600, mode='determinate', maximum=100)
        self.progress.grid(row=8, column=0, columnspan=3, pady=4)

        self.refresh()

    def log_write(self, txt):
        def _write():
            self.log.insert(END, txt)
            self.log.see('end')
        self.root.after(0, _write)

    def set_progress(self, pct: int):
        if pct < 0:
            pct = 0
        if pct > 100:
            pct = 100
        def _set():
            self.progress['value'] = pct
        self.root.after(0, _set)

    def refresh(self):
        self.lb.delete(0, END)
        devs, err = get_block_devices()
        if err:
            self.log_write(err + "\n")
            return
        self.devs = devs
        for d in devs:
            self.lb.insert(END, device_display(d))
        self.log_write("Refreshed device list.\n")

    def on_format(self):
        sel = self.lb.curselection()
        if not sel:
            messagebox.showwarning("No device", "Please select a device to format.")
            return
        idx = sel[0]
        dev = self.devs[idx]
        devname = dev.get("name")
        removable = dev.get("rm")
        fs_key = self.fsvar.get()
        mkcmd = self.fsmap.get(fs_key)
        label = self.label_entry.get().strip()

        msg = (
            f"You are about to irreversibly format /dev/{devname} as {fs_key}.\n"
            "All data on this device will be lost.\n\n"
            "Do you want to continue?"
        )
        if not messagebox.askyesno("Confirm format", msg):
            return

        parts = dev.get("children") or []
        action = None
        target_node = None
        if parts:
            part_names = [p.get("name") for p in parts if p.get("type") == "part"]
            first_part = part_names[0] if part_names else None
            if first_part:
                q = messagebox.askyesnocancel("Partition detected",
                                              f"Device has partition(s) (e.g. /dev/{first_part}).\nYes: format the first partition (/dev/{first_part}).\nNo: re-create partition table and make a single partition to format.\nCancel: abort.")
                if q is None:
                    return
                if q is True:
                    action = 'format_partition'
                    target_node = first_part
                else:
                    action = 'repartition_and_format'
        else:
            action = 'create_and_format'

        # disable UI and start progress
        self.format_btn.config(state='disabled')
        self.set_progress(0)

        def worker():
            try:
                if action == 'format_partition':
                    self.root.after(0, lambda: self.log_write(f"Starting format of /dev/{target_node} -> {fs_key}\n"))
                    run_format(target_node, mkcmd, fs_key, label, self.log_write, progress_cb=self.set_progress)
                else:
                    # create a partition then format it using selected label type
                    label_type = self.part_label_map.get(self.part_label_var.get(), 'msdos')
                    newp = create_single_partition(f"/dev/{devname}", self.log_write, label_type=label_type, progress_cb=self.set_progress)
                    if not newp:
                        self.root.after(0, lambda: messagebox.showerror("Partition error", "Failed to create partition. Aborting."))
                        return
                    self.root.after(0, lambda: self.log_write(f"Starting format of /dev/{newp} -> {fs_key}\n"))
                    run_format(newp, mkcmd, fs_key, label, self.log_write, progress_cb=self.set_progress)
            finally:
                def finish():
                    self.set_progress(100)
                    self.format_btn.config(state='normal')
                self.root.after(0, finish)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def on_write_iso(self):
        sel = self.lb.curselection()
        if not sel:
            messagebox.showwarning("No device", "Please select a device to write the ISO to.")
            return
        idx = sel[0]
        dev = self.devs[idx]
        devname = dev.get("name")

        iso_path = filedialog.askopenfilename(title="Select bootable ISO", filetypes=[("ISO files", "*.iso"), ("All files", "*")])
        if not iso_path:
            return
        # Always compute SHA-256 of the ISO before writing (automated)
        compute_hash = True

        def do_write():
            msg = (
                f"You are about to write ISO:\n{iso_path}\n\nto /dev/{devname}.\nThis will overwrite the entire device and make it bootable (if the ISO is hybrid).\n\nContinue?"
            )
            if not messagebox.askyesno("Confirm write ISO", msg):
                return

            # disable UI and reset progress
            self.format_btn.config(state='disabled')
            self.iso_btn.config(state='disabled')
            self.set_progress(0)

            def worker_iso():
                try:
                    write_iso_to_device(devname, iso_path, self.log_write, progress_cb=self.set_progress)
                    # after writing, ask user if they want to mount to inspect files
                    def ask_mount():
                        try:
                            ok = messagebox.askyesno("Mount device?", "Do you want to mount the device to inspect files?")
                        except Exception:
                            ok = False
                        if ok:
                            # mount first partition if available, else device
                            parts = dev.get("children") or []
                            target = None
                            if parts:
                                part_names = [p.get("name") for p in parts if p.get("type") == "part"]
                                target = part_names[0] if part_names else None
                            if not target:
                                # try to detect first partition
                                target = find_first_partition(devname)
                            if target:
                                # run mount in background
                                def mount_thread():
                                    mount_first_partition(target, self.log_write)
                                threading.Thread(target=mount_thread, daemon=True).start()
                            else:
                                # try mounting whole device
                                def mount_thread2():
                                    mount_first_partition(devname, self.log_write)
                                threading.Thread(target=mount_thread2, daemon=True).start()
                    self.root.after(0, ask_mount)
                finally:
                    def finish():
                        self.set_progress(100)
                        self.format_btn.config(state='normal')
                        self.iso_btn.config(state='normal')
                    self.root.after(0, finish)

            t = threading.Thread(target=worker_iso, daemon=True)
            t.start()

        if compute_hash:
            # disable UI while hashing
            self.format_btn.config(state='disabled')
            self.iso_btn.config(state='disabled')
            self.set_progress(0)

            def hash_worker():
                try:
                    digest = compute_iso_sha256(iso_path, self.log_write, progress_cb=self.set_progress)
                    if digest:
                        iso_name = os.path.basename(iso_path)
                        # try online fetch of checksum
                        online_digest = fetch_online_sha256(iso_name, self.log_write)
                        if online_digest:
                            if online_digest.strip().lower() != digest.strip().lower():
                                self.log_write("WARNING: Online checksum does not match computed checksum. Proceeding to write (force).\n")
                            else:
                                self.log_write("Online checksum matches computed SHA-256.\n")
                        else:
                            # fallback to local checksum file detection
                            chk_file, expected = find_checksum_file(iso_path)
                            if chk_file and expected:
                                if expected.strip().lower() != digest.strip().lower():
                                    self.log_write("WARNING: Local checksum file does not match computed checksum. Proceeding to write (force).\n")
                                else:
                                    self.log_write("Local checksum matches computed SHA-256.\n")
                            else:
                                self.log_write("No online checksum found; no local checksum found. Proceeding to write.\n")
                    # continue to write regardless (force)
                    do_write()
                finally:
                    def finish_hash():
                        self.set_progress(0)
                        self.format_btn.config(state='normal')
                        self.iso_btn.config(state='normal')
                    self.root.after(0, finish_hash)

            t = threading.Thread(target=hash_worker, daemon=True)
            t.start()
        else:
            do_write()


if __name__ == '__main__':
    ok = check_and_install_dependencies()
    if not ok:
        # Write final note to install log and exit
        write_install_log('Dependencies missing and could not be auto-installed; exiting application')
        print('Missing dependencies. See', INSTALL_LOG, 'for instructions.')
        sys.exit(1)

    # Now import tkinter widgets into globals (safe after dependencies are present)
    from tkinter import Tk, Listbox, StringVar, OptionMenu, Button, Label, Entry, Text, END, Scrollbar, RIGHT, Y, BOTH, Frame, messagebox, filedialog, simpledialog
    from tkinter import ttk

    # Splash screen support: look for a splash image named 'splash.png' next to the script
    SPLASH_DEFAULT = Path(__file__).with_name('splash.png')

    def show_splash(image_path, timeout_ms=2000):
        # small transient root to show splash
        sroot = Tk()
        sroot.overrideredirect(True)
        try:
            if PIL_AVAILABLE:
                img = Image.open(image_path)
                tkimg = ImageTk.PhotoImage(img)
            else:
                tkimg = None
        except Exception:
            tkimg = None

        if tkimg:
            lbl = Label(sroot, image=tkimg)
            lbl.image = tkimg
            lbl.pack()
            sroot.update_idletasks()
            # center
            w = lbl.winfo_width()
            h = lbl.winfo_height()
            sw = sroot.winfo_screenwidth()
            sh = sroot.winfo_screenheight()
            x = int((sw - w) / 2)
            y = int((sh - h) / 2)
            sroot.geometry(f"{w}x{h}+{x}+{y}")
        else:
            lbl = Label(sroot, text="Starting...")
            lbl.pack(padx=20, pady=20)

        def close():
            try:
                sroot.destroy()
            except Exception:
                pass

        sroot.after(timeout_ms, close)
        # allow click to close
        lbl.bind("<Button-1>", lambda e: close())
        sroot.mainloop()

    # If a splash image exists, show it; otherwise ask the user if they'd like to select one
    splash_to_show = None
    if SPLASH_DEFAULT.exists():
        splash_to_show = str(SPLASH_DEFAULT)
    else:
        # create a tiny root to ask user whether to select an image
        tmp_root = Tk()
        tmp_root.withdraw()
        if messagebox.askyesno("Splash image", "No default splash image found. Do you want to select an image to show at startup?"):
            p = filedialog.askopenfilename(title="Select splash image (optional)", filetypes=[("Image files","*.png;*.jpg;*.jpeg;*.gif;*.bmp"), ("All files","*")])
            if p:
                splash_to_show = p
        tmp_root.destroy()

    if splash_to_show:
        try:
            show_splash(splash_to_show, timeout_ms=2000)
        except Exception as e:
            print("Could not show splash:", e)

    root = Tk()
    app = App(root)
    root.mainloop()
