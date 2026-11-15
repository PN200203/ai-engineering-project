"""
THE ALLOW-LIST. The LLM (or the rule planner) can only name a probe from PROBES; it never
supplies argv, flags or paths. Execution is shell=False, fixed argv, LC_ALL=C, timeout, output cap.
Probes are read-only by construction; serials/UUIDs/MACs are deliberately not collected.

Root handling (3 layers, least privilege first):
  1. no-root sources   (/proc, /sys/class/dmi/id, lsblk, lspci ...)
  2. --dmi-file        a one-time `sudo dmidecode > dmi.txt` snapshot the user made themselves
  3. --sudo            `sudo -v` once at startup (user types password to sudo, never to us),
                       then ONLY allow-listed argv run as `sudo -n <argv>`
Otherwise the fact is recorded as unknown:needs_root and the answer says exactly that.
"""
import glob, os, shutil, subprocess
from dataclasses import dataclass, field
from . import parsers as P

def _read(path):
    with open(path) as f: return f.read()
def _sys_dmi():
    keep = ("sys_vendor", "product_name", "bios_version", "bios_date", "board_vendor", "board_name")   # no *_serial / *_uuid
    out = []
    for k in keep:
        try: out.append(f"{k}={_read('/sys/class/dmi/id/' + k).strip()}")
        except OSError: pass
    return "\n".join(out)
def _battery():
    for d in sorted(glob.glob("/sys/class/power_supply/BAT*")):
        out = []
        for k in ("status", "capacity", "energy_full", "energy_full_design", "cycle_count"):
            try: out.append(f"{k}={_read(d + '/' + k).strip()}")
            except OSError: pass
        return "\n".join(out)
    raise FileNotFoundError("no battery")
def _sysblock():
    rows = []
    for d in sorted(glob.glob("/sys/block/*")):
        n = os.path.basename(d)
        if n.startswith(("loop", "ram", "zram", "dm-")): continue
        try: rows.append(f"name={n};size={_read(d + '/size').strip()};rota={_read(d + '/queue/rotational').strip()}")
        except OSError: pass
    return "\n".join(rows)

@dataclass
class Probe:
    name: str; desc: str; parser: object; argv: list = None; reader: object = None
    needs_root: bool = False; fallback: str = None; dmi: bool = False

PROBES = {p.name: p for p in [
    Probe("cpu", "CPU model/cores", P.parse_cpu, argv=["lscpu"], fallback="cpuinfo"),
    Probe("cpuinfo", "CPU via /proc", P.parse_cpuinfo, reader=lambda: _read("/proc/cpuinfo")),
    Probe("memory", "usable RAM", P.parse_meminfo, reader=lambda: _read("/proc/meminfo")),
    Probe("mem_slots", "DIMM slots", P.parse_mem_slots, argv=["dmidecode", "-t", "17"], needs_root=True, dmi=True),
    Probe("mem_max", "max RAM supported by board", P.parse_mem_max, argv=["dmidecode", "-t", "16"], needs_root=True, dmi=True),
    Probe("storage", "disks", P.parse_storage, argv=["lsblk", "-J", "-b", "-d", "-o", "NAME,TYPE,SIZE,ROTA,TRAN,MODEL"], fallback="sysblock"),
    Probe("sysblock", "disks via /sys", P.parse_sysblock, reader=_sysblock),
    Probe("gpu", "GPUs", P.parse_gpu, argv=["lspci", "-mm"]),
    Probe("system", "vendor/model/BIOS", P.parse_kv_lines, reader=_sys_dmi),
    Probe("os", "distro", P.parse_os, reader=lambda: _read("/etc/os-release")),
    Probe("kernel", "kernel", P.parse_kernel, argv=["uname", "-r"]),
    Probe("battery", "battery health", P.parse_battery, reader=_battery),
]}

@dataclass
class Result:
    probe: str; status: str; data: dict = field(default_factory=dict); via: str = ""   # ok|needs_root|missing_tool|error|absent

class Runner:
    def __init__(self, use_sudo=False, dmi_file=None, fixtures=None, timeout=8):
        self.use_sudo, self.dmi_file, self.fixtures, self.timeout = use_sudo, dmi_file, fixtures, timeout
        self.cache, self.trace = {}, []

    def run(self, name):
        if name not in PROBES: raise KeyError(f"probe '{name}' is not allow-listed")      # hard stop for hallucinated tool names
        if name in self.cache: return self.cache[name]
        res = self._run(PROBES[name]); self.trace.append(f"{name}: {res.status} ({res.via})"); self.cache[name] = res
        return res

    def _run(self, p):
        status, text, via = self._collect(p)
        if status != "ok" and p.fallback:
            fb = self._run(PROBES[p.fallback])
            if fb.status == "ok": fb.via = f"fallback:{p.fallback}"; fb.probe = p.name; return fb
        if status != "ok": return Result(p.name, status, {}, via)
        try: return Result(p.name, "ok", p.parser(text), via)
        except Exception as e: return Result(p.name, "error", {}, f"parse failed: {type(e).__name__}")

    def _collect(self, p):
        if self.fixtures:                                         # test/portability mode: replay a recorded machine
            f = os.path.join(self.fixtures, p.name + ".txt")
            if os.path.exists(os.path.join(self.fixtures, p.name + ".NEEDS_ROOT")): return "needs_root", "", "fixture"
            return ("ok", _read(f), "fixture") if os.path.exists(f) else ("missing_tool", "", "fixture")
        if p.dmi and self.dmi_file:
            try: return "ok", _read(self.dmi_file), "snapshot"
            except OSError: return "error", "", "snapshot unreadable"
        try:
            if p.reader: return "ok", p.reader(), "sysfs/procfs"
            argv = list(p.argv)
            if p.needs_root and os.geteuid() != 0:
                if not self.use_sudo: return "needs_root", "", "run with --sudo or --dmi-file"
                argv = ["sudo", "-n"] + argv
            if shutil.which(p.argv[0]) is None: return "missing_tool", "", f"{p.argv[0]} not installed"
            r = subprocess.run(argv, capture_output=True, text=True, timeout=self.timeout, shell=False, env={"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
            if r.returncode != 0:
                if "password" in r.stderr or "sudo" in r.stderr: return "needs_root", "", "sudo not authorised"
                return "error", "", r.stderr.strip()[:80]
            return "ok", r.stdout[:200_000], "exec"
        except FileNotFoundError: return "missing_tool", "", f"{p.argv[0] if p.argv else 'file'} unavailable"
        except subprocess.TimeoutExpired: return "error", "", "timeout"
        except OSError as e: return "error", "", str(e)[:60]
