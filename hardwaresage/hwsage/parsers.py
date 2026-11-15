"""Raw tool output -> structured dicts. Pure functions: trivially testable against fixtures."""
import json, re, shlex

def kv(text, sep=":"):
    d = {}
    for line in text.splitlines():
        if sep in line:
            k, v = line.split(sep, 1); d.setdefault(k.strip(), v.strip())
    return d

UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
def to_bytes(s):
    m = re.match(r"\s*(\d+(?:\.\d+)?)\s*([kmgt]?b)\b", s or "", re.I)
    return int(float(m.group(1)) * UNITS[m.group(2).lower()]) if m else None

def parse_cpu(t):
    d = kv(t); out = {"model": d.get("Model name"), "arch": d.get("Architecture"),
        "logical": int(d.get("CPU(s)", 0)) or None, "threads_per_core": int(d.get("Thread(s) per core", 0)) or None,
        "cores_per_socket": int(d.get("Core(s) per socket", 0)) or None, "sockets": int(d.get("Socket(s)", 0)) or None}
    if d.get("CPU max MHz"): out["max_mhz"] = round(float(d["CPU max MHz"]))
    return out

def parse_cpuinfo(t):
    models = re.findall(r"^model name\s*:\s*(.+)$", t, re.M)
    return {"model": models[0] if models else None, "logical": len(models) or None}

def parse_meminfo(t):
    d = kv(t); tot = int(d["MemTotal"].split()[0]) * 1024 if "MemTotal" in d else None
    av = int(d["MemAvailable"].split()[0]) * 1024 if "MemAvailable" in d else None
    return {"usable_bytes": tot, "available_bytes": av}

def dmi_blocks(text):
    blocks, cur = [], None
    for line in text.splitlines():
        if line.startswith("Handle "): cur = {"title": None, "f": {}}; blocks.append(cur); continue
        if cur is None: continue
        if cur["title"] is None and line.strip(): cur["title"] = line.strip(); continue
        if ":" in line: k, v = line.strip().split(":", 1); cur["f"].setdefault(k.strip(), v.strip())
    return blocks

def parse_mem_slots(t):          # works for `dmidecode -t 17` and for a full dmidecode snapshot
    slots = []
    for b in dmi_blocks(t):
        if b["title"] != "Memory Device": continue
        f = b["f"]; size = to_bytes(f.get("Size", ""))
        slots.append({"locator": f.get("Locator"), "size_bytes": size, "type": f.get("Type") if size else None,
                      "form": f.get("Form Factor"), "speed": f.get("Configured Memory Speed") or f.get("Speed") if size else None})
    return {"slots": slots}          # NB: serial numbers are never extracted (privacy)

def parse_mem_max(t):
    for b in dmi_blocks(t):
        if b["title"] == "Physical Memory Array" and b["f"].get("Use") == "System Memory":
            return {"max_bytes": to_bytes(b["f"].get("Maximum Capacity", "")), "slot_count": int(b["f"].get("Number Of Devices", 0)) or None}
    return {}

def parse_storage(t):
    devs = []
    for d in json.loads(t)["blockdevices"]:
        if d.get("type") != "disk" or d["name"].startswith(("zram", "loop", "ram")): continue
        rota = d.get("rota"); rota = rota in (True, 1, "1", "true")
        tran = (d.get("tran") or ("nvme" if d["name"].startswith("nvme") else None))
        kind = "NVMe SSD" if tran == "nvme" else ("HDD" if rota else "SSD")
        devs.append({"name": d["name"], "size_bytes": int(d["size"]), "kind": kind, "bus": tran, "model": (d.get("model") or "").strip()})
    return {"disks": devs}

def parse_sysblock(t):           # fallback when lsblk is missing: "name=..;size=..;rota=..;model=.."
    devs = []
    for line in t.splitlines():
        d = dict(p.split("=", 1) for p in line.split(";") if "=" in p)
        if d: devs.append({"name": d["name"], "size_bytes": int(d["size"]) * 512, "kind": "NVMe SSD" if d["name"].startswith("nvme") else ("HDD" if d.get("rota") == "1" else "SSD"),
                           "bus": "nvme" if d["name"].startswith("nvme") else None, "model": d.get("model", "")})
    return {"disks": devs}

def parse_gpu(t):
    gpus = []
    for line in t.splitlines():
        try: p = shlex.split(line)
        except ValueError: continue
        if len(p) >= 4 and any(k in p[1] for k in ("VGA", "3D", "Display")):
            gpus.append({"kind": "integrated/primary" if "VGA" in p[1] else "discrete/secondary", "vendor": p[2], "model": p[3]})
    return {"gpus": gpus}

def parse_kv_lines(t): return {k: v for k, v in (l.split("=", 1) for l in t.splitlines() if "=" in l)}
def parse_os(t): return {"pretty": parse_kv_lines(t).get("PRETTY_NAME", "").strip('"') or None}
def parse_kernel(t): return {"release": t.strip()}
def parse_battery(t):
    d = {k: int(v) if v.lstrip("-").isdigit() else v for k, v in parse_kv_lines(t).items()}
    if d.get("energy_full") and d.get("energy_full_design"): d["health_pct"] = round(100 * d["energy_full"] / d["energy_full_design"])
    return d
