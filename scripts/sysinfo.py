#!/usr/bin/env python3
"""READ-ONLY: what machine is this? Run it first: it tells the agent which reference to follow.

Prints OS (windows / macos / linux / wsl), version, CPU architecture, RAM, free space, Python,
which helper tools are installed, WSL distros (on Windows), cloud-sync folders, coding-agent
configs found, and the reference file + shell scripts to use next.
Usage: python3 -I sysinfo.py [--out DIR]
"""
import argparse
import os
import platform
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcm_common as C  # noqa: E402

TOOLS = ["git", "docker", "brew", "smartctl", "wsl", "powershell", "pwsh", "gio", "trash-put", "osascript", "journalctl",
         "snap", "flatpak", "apt-get", "dnf", "pacman", "zypper", "npm", "pnpm", "yarn", "bun", "pip", "uv", "go", "cargo",
         "dotnet", "ollama", "code", "claude", "codex", "cursor", "gemini"]


def ram():
    """(total_bytes, available_bytes) or (None, None)."""
    try:
        if C.IS_WIN:
            import ctypes

            class MEMSTAT(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong), ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEMSTAT()
            m.dwLength = ctypes.sizeof(MEMSTAT)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return m.ullTotalPhys, m.ullAvailPhys
        elif C.IS_MAC:
            rc, total = C.run(["sysctl", "-n", "hw.memsize"], timeout=10)
            rc2, vm = C.run(["vm_stat"], timeout=10)
            page = 4096
            avail = 0
            for line in vm.splitlines():
                if "page size of" in line:
                    page = int(line.split("page size of")[1].split()[0])
                for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"):
                    if line.startswith(k + ":"):
                        avail += int(line.split(":")[1].strip().rstrip("."))
            return int(total.strip()), avail * page
        else:
            info = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    info[k] = int(v.split()[0]) * 1024
            return info.get("MemTotal"), info.get("MemAvailable")
    except Exception:
        pass
    return None, None


def os_version():
    if C.IS_WIN:
        return "Windows %s (build %s)" % (platform.release(), platform.version())
    if C.IS_MAC:
        return "macOS %s" % platform.mac_ver()[0]
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def wsl_distros():
    exe = C.which("wsl") or C.which("wsl.exe")
    if not exe:
        return []
    env = dict(os.environ, WSL_UTF8="1")
    rc, out = C.run([exe, "--list", "--verbose"], timeout=30, env=env)
    rows = []
    for line in out.replace("\x00", "").splitlines()[1:]:
        parts = line.replace("*", " ").split()
        if len(parts) >= 3:
            rows.append({"name": parts[0], "state": parts[1], "version": parts[2], "default": line.strip().startswith("*")})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    label = C.os_label()
    total, avail = ram()
    home_free = C.free_bytes(C.HOME)
    tools = dict((t, bool(C.which(t) or (C.IS_WIN and C.which(t + ".exe")))) for t in TOOLS)
    agents = sorted(set(lbl for lbl, p, _ in C.agent_config_files() if os.path.isfile(p)))
    ref = {"windows": "references/windows.md", "macos": "references/macos.md", "linux": "references/linux.md",
           "wsl": "references/linux.md + references/wsl.md"}.get(label, "references/linux.md")
    shells = {"windows": "scripts/windows/*.ps1", "macos": "scripts/macos/*.sh", "linux": "scripts/linux/*.sh",
              "wsl": "scripts/linux/*.sh (inside the distro); Windows side: scripts/windows + scripts/wsl"}.get(label, "scripts/linux/*.sh")
    data = {"os": label, "version": os_version(), "arch": platform.machine(), "python": platform.python_version(),
            "home": C.HOME, "home_free_bytes": home_free, "ram_total": total, "ram_available": avail,
            "cpu_count": os.cpu_count(), "tools": tools, "cloud_sync_folders": C.cloud_roots(), "agent_configs": agents,
            "runs_dir": C.runs_dir(), "reference": ref, "shell_scripts": shells}
    if C.IS_WIN:
        data["wsl_distros"] = wsl_distros()
        data["fixed_drives"] = [{"drive": d, "free": C.free_bytes(d)} for d in C.fixed_drives()]
    if label == "wsl":
        data["wsl_distro"] = os.environ.get("WSL_DISTRO_NAME", "")
    lines = ["OS: %s | %s | %s | Python %s" % (label, data["version"], data["arch"], data["python"]),
             "home: %s | free there: %s" % (C.HOME, C.human(home_free)),
             "RAM: %s total, %s available" % (C.human(total), C.human(avail))]
    for d in data.get("fixed_drives", []):
        lines.append("drive %s free %s" % (d["drive"], C.human(d["free"])))
    for d in data.get("wsl_distros", []):
        lines.append("WSL distro: %s (%s, WSL%s)" % (d["name"], d["state"], d["version"]))
    if label == "wsl":
        lines.append("running INSIDE WSL distro %s: this is Linux; the Windows side needs its own run" % data["wsl_distro"])
    lines.append("tools: " + " ".join(t for t, ok in tools.items() if ok))
    lines.append("cloud-sync folders (never delete inside): " + (", ".join(C.tilde(p) for p in data["cloud_sync_folders"]) or "none found"))
    lines.append("agent configs found: " + (", ".join(agents) or "none"))
    lines.append("NEXT: read %s; shell helpers: %s; run outputs go to %s" % (ref, shells, C.runs_dir()))
    if a.out:
        C.finish(a.out, "sysinfo", data, lines)
    else:
        print("\n".join(lines))


if __name__ == "__main__":
    main()
