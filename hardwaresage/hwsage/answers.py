"""
Deterministic answer composers. They read ONLY the ledger, state limits explicitly, and
register every derived number back into the ledger (so the grounding check can trust it).
Each returns (text, evidence_fact_ids).
"""
import math

STD = [2, 4, 8, 16, 32, 64, 128]          # standard single-module sizes
TOTALS = [2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128]
def gb(b): return f"{b / 2**30:.0f} GB" if abs(b / 2**30 - round(b / 2**30)) < .05 else f"{b / 2**30:.1f} GB"
def tb_or_gb(b): return f"{b / 1e12:.1f} TB" if b >= 1e12 else f"{b / 1e9:.0f} GB"     # disks are marketed in decimal units
def need_hint(L, ids):
    for i in ids:
        w = L.why_unknown(i)
        if w == "needs_root": return " I can't read the memory-slot table without root: re-run with --sudo, or give me a snapshot (`sudo dmidecode > dmi.txt` then --dmi-file dmi.txt)."
        if w == "missing_tool": return " (A required tool isn't installed - e.g. `dmidecode`, `lspci` from pciutils, or `lsblk` from util-linux.)"
        if w: return f" (I couldn't read it: {w}.)"
    return ""

def memory(L):
    u = L.get("memory.usable_bytes")
    if u is None: return "I couldn't read memory information on this system.", []
    inst = L.get("memory.installed_bytes")
    if inst: return f"You have {gb(inst)} of RAM installed ({u / 2**30:.1f} GiB usable by Linux).", ["memory.installed_bytes", "memory.usable_bytes"]
    approx = next((s for s in TOTALS if s * 2**30 >= u), None)
    L.put("memory.approx_installed_gb", approx, "GB", "derived", "derived")
    return f"About {approx} GB of RAM ({u / 2**30:.1f} GiB usable). I can't see the exact module layout without root.", ["memory.usable_bytes"]

def memory_upgrade(L):
    slots, mx, ns = L.get("memslots.slots"), L.get("memslots.max_bytes"), L.get("memslots.count")
    if slots is None or mx is None:
        u = L.get("memory.usable_bytes"); base = f"You currently have about {math.ceil(u / 2**30)} GB usable. " if u else ""
        return base + "I can't tell if you can add RAM, because slot count and board maximum need root-only DMI data." + need_hint(L, ["memslots.slots", "memslots.max_bytes"]), []
    used = [s for s in slots if s["size_bytes"]]; free = len(slots) - len(used); inst = sum(s["size_bytes"] for s in used)
    L.put("memory.installed_bytes", inst, "bytes", "derived", "derived"); L.put("mem.slots_used", len(used), "", "derived", "derived"); L.put("mem.slots_total", len(slots), "", "derived", "derived")
    soldered = any((s.get("form") or "").lower() in ("row of chips", "chip", "die") for s in slots)
    if soldered: return f"Your {gb(inst)} appears to be soldered to the board (form factor reported as chips), so it is probably not upgradeable.", ["memslots.slots"]
    head = max(0, mx - inst); L.put("mem.headroom_gb", round(head / 2**30), "GB", "derived", "derived")
    kind = " ".join(x for x in {used[0].get("type"), used[0].get("form"), used[0].get("speed")} if x) if used else ""
    if free == 0: return f"You have {gb(inst)} in {len(used)} of {len(slots)} slots - all slots are full. To add RAM you'd have to replace modules (board max: {gb(mx)}).", ["memslots.slots", "memslots.max_bytes"]
    per = head / free; best = max((s for s in STD if s * 2**30 <= per), default=None)
    L.put("mem.max_stick_gb", best, "GB", "derived", "derived")
    txt = f"You have {gb(inst)} in {len(used)} of {len(slots)} slots. The board supports up to {gb(mx)}, so you can add " + (f"a stick of up to {best} GB" if free == 1 else f"{free} sticks of up to {best} GB each") + "."
    return txt + (f" Match the existing type ({kind})." if kind else "") + " Caveat: the board maximum reported by firmware is sometimes conservative or optimistic - check the vendor's spec sheet before buying.", ["memslots.slots", "memslots.max_bytes", "mem.max_stick_gb"]

def cpu(L):
    m = L.get("cpu.model")
    if not m: return "I couldn't read CPU details." , []
    parts = [f"{m}"]; c, t, l = L.get("cpu.cores_per_socket"), L.get("cpu.threads_per_core"), L.get("cpu.logical")
    if c and L.get("cpu.sockets"): parts.append(f"{c * L.get('cpu.sockets')} cores / {l} threads")
    elif l: parts.append(f"{l} logical CPUs (core/thread split unavailable)")
    if L.get("cpu.max_mhz"): parts.append(f"up to {L.get('cpu.max_mhz') / 1000:.1f} GHz")
    return "Your CPU is " + ", ".join(parts) + ".", ["cpu.model"]

def storage(L, q=""):
    d = L.get("storage.disks")
    if d is None: return "I couldn't enumerate disks." + need_hint(L, ["storage.disks"]), []
    lines = [f"{x['name']}: {tb_or_gb(x['size_bytes'])} {x['kind']}" + (f" ({x['model']})" if x["model"] else "") for x in d]
    ql = q.lower()
    if "nvme" in ql:
        nv = [x for x in d if x["kind"] == "NVMe SSD"]
        head = f"Yes - {len(nv)} NVMe drive(s)." if nv else "No NVMe drive found."
        return head + " " + "; ".join(lines) + ".", ["storage.disks"]
    if "ssd" in ql:
        ssd = [x for x in d if "SSD" in x["kind"]]
        return ("Yes, " if ssd else "No SSD found. ") + "; ".join(lines) + ".", ["storage.disks"]
    return "Disks: " + "; ".join(lines) + ".", ["storage.disks"]

def gpu(L):
    g = L.get("gpu.gpus")
    if g is None: return "I couldn't list GPUs." + need_hint(L, ["gpu.gpus"]) + " (`lspci` comes from the pciutils package.)", []
    if not g: return "No GPU was reported by lspci.", ["gpu.gpus"]
    return "Graphics: " + "; ".join(f"{x['vendor']} {x['model']} ({x['kind']})" for x in g) + ".", ["gpu.gpus"]

def battery(L):
    if L.get("battery.capacity") is None: return "No battery information found (desktop, or not exposed by the kernel).", []
    h = L.get("battery.health_pct"); c = L.get("battery.cycle_count")
    return f"Battery is at {L.get('battery.capacity')}% ({L.get('battery.status')})" + (f", holding about {h}% of its design capacity" if h else "") + (f" after {c} cycles" if c else "") + ".", ["battery.capacity"]

def system(L):
    v, m = L.get("system.sys_vendor"), L.get("system.product_name")
    os_, k = L.get("os.pretty"), L.get("kernel.release")
    bits = [f"{v} {m}".strip() if m else None, f"BIOS {L.get('system.bios_version')}" if L.get("system.bios_version") else None, f"{os_}" if os_ else None, f"kernel {k}" if k else None]
    bits = [b for b in bits if b]
    return ("This machine: " + ", ".join(bits) + ".") if bits else "I couldn't read system information.", []

INTENTS = {   # intent -> (probes needed, composer, fact namespace -> probe)
    "memory": (["memory", "mem_slots"], memory), "memory_upgrade": (["memory", "mem_slots", "mem_max"], memory_upgrade),
    "cpu": (["cpu"], cpu), "storage": (["storage"], storage), "gpu": (["gpu"], gpu), "battery": (["battery"], battery),
    "system": (["system", "os", "kernel"], system),
}
