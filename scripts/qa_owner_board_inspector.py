"""Fixed-scope read-only evidence probe for the PIM recovery adapter.

This module has NO CLI, no board write, no SSH credential acquisition,
no reboot, no deletion and no campaign admission. A separately reviewed
owner may supply an already-open SSH client *inside* official board lease.

Board evidence is not a recovery success decision. The full session
EOS/fsync, product source provenance and manual deployment authority
remain separate obligations.
"""
from __future__ import annotations

import json
import shlex

from scripts.qa_owner_board_adapter import AdapterBlocked, BoardSample, TARGET

REMOTE_PROBE = r'''
import hashlib, json, os, re, stat, subprocess, time
from pathlib import Path

TARGET = "192.168.214.4"
ROOT = Path("/root/shared_v/pim-check-recovery")
CONFIG = "/root/shared_v/edgeconf_pim.json"
ORD = "/root/shared_v/ord_vcm_conf.json"
RUNTIME = "/run/pim-camera/config/pim_runtime.json"
HEALTH = "/run/pim-camera/gstApp.json"
MAX_DATA = 4 * 1024 * 1024

def read_plain(path, limit=MAX_DATA, *, owner=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
            raise ValueError("not a bounded plain file")
        if owner is not None and before.st_uid != owner:
            raise ValueError("file owner mismatch")
        chunks = bytearray()
        while True:
            item = os.read(fd, min(65536, limit + 1 - len(chunks)))
            if not item:
                break
            chunks.extend(item)
            if len(chunks) > limit:
                raise ValueError("file larger than maximum")
        after = os.fstat(fd)
        if (before.st_ino, before.st_dev, before.st_mtime_ns, before.st_size) != (
                after.st_ino, after.st_dev, after.st_mtime_ns, after.st_size):
            raise ValueError("file changed while observing")
        return bytes(chunks)
    finally:
        os.close(fd)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def sha_executable(path):
    # /proc/pid/exe is an intentional kernel link. Only actual process
    # identities selected by fixed comm names are followed.
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("not a regular executable")
        h = hashlib.sha256()
        amount = 0
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            amount += len(block)
            if amount > 128 * 1024 * 1024:
                raise ValueError("unbounded executable size")
            h.update(block)
        after = os.fstat(fd)
        if (before.st_ino, before.st_dev, before.st_size) != (
                after.st_ino, after.st_dev, after.st_size):
            raise ValueError("executable changed while reading")
        return h.hexdigest()
    finally:
        os.close(fd)

def process_pids():
    names = {"gstApp": [], "ord": [], "vcm": []}
    pids = []
    for path in Path("/proc").iterdir():
        if not path.name.isdecimal():
            continue
        pid = int(path.name)
        try:
            name = read_plain(f"/proc/{pid}/comm", limit=256).decode().strip()
        except (FileNotFoundError, ProcessLookupError):
            continue
        pids.append(pid)
        if name in names:
            names[name].append(pid)
    return names, pids

def journal():
    try:
        status = ROOT.lstat()
    except FileNotFoundError:
        return ("ABSENT", None, None, None)
    if (not stat.S_ISDIR(status.st_mode) or status.st_uid != 0
            or stat.S_IMODE(status.st_mode) != 0o700):
        raise ValueError("unsafe recovery directory")
    entries = sorted(os.listdir(ROOT))
    if not entries:
        return ("ABSENT", None, None, None)
    if len(entries) != 1 or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", entries[0]):
        raise ValueError("untrusted journal directory contents")
    name = entries[0]
    directory = ROOT / name
    st = directory.lstat()
    if (not stat.S_ISDIR(st.st_mode) or st.st_uid != 0
            or stat.S_IMODE(st.st_mode) != 0o700
            or sorted(os.listdir(directory)) != ["edgeconf_pim.json.original", "manifest.json"]):
        raise ValueError("unsafe journal entry")
    raw_manifest = read_plain(directory / "manifest.json", owner=0)
    raw_original = read_plain(directory / "edgeconf_pim.json.original", owner=0)
    for file in (directory / "manifest.json", directory / "edgeconf_pim.json.original"):
        if stat.S_IMODE(file.lstat().st_mode) != 0o600:
            raise ValueError("unsafe journal file permissions")
    meta = json.loads(raw_manifest)
    if (not isinstance(meta, dict) or set(meta) != {
        "schema_version", "run_id", "config_path", "original_sha256",
        "created_at", "state"
    } or meta["schema_version"] != 1 or meta["run_id"] != name
        or meta["config_path"] != CONFIG or meta["original_sha256"] != digest(raw_original)
        or meta["state"] not in {
            "JOURNALED", "APPLIED", "REBOOTED", "RESTORED", "VERIFIED", "CLOSED"
        }):
        raise ValueError("untrusted journal manifest")
    json.loads(raw_original)
    return ("DIRTY", meta["state"], name, meta["original_sha256"])

def no_writer_handles(pids):
    # No writable FD to active media storage in ANY accessible process.
    # Any opaque permission/read race returns False (never false closure proof).
    for pid in pids:
        folder = Path(f"/proc/{pid}/fd")
        try:
            entries = list(folder.iterdir())
        except FileNotFoundError:
            continue
        except OSError:
            return False
        for fd in entries:
            try:
                linked = os.readlink(fd)
                raw = read_plain(f"/proc/{pid}/fdinfo/{fd.name}", limit=1024)
                flags = next(int(line.split()[1], 8) for line in raw.decode().splitlines()
                             if line.startswith("flags:"))
                writable = flags & os.O_ACCMODE in (os.O_WRONLY, os.O_RDWR)
                if writable and linked.startswith(
                    ("/mnt/sd_cam/", "/dev/shm/recordings/", "/dev/shm/")):
                    return False
            except FileNotFoundError:
                continue
            except (OSError, ValueError, StopIteration, UnicodeError):
                return False
    return True

def healthy_consumer(owner, processes, snapshot, boot):
    if owner.get("owner", {}).get("lifecycle") != "ACTIVE":
        return False
    if any(len(procs) != 1 for procs in processes.values()):
        return False
    try:
        state = read_plain("/tmp/cam_state/state", limit=64).decode().strip()
        timestamp = int(read_plain("/tmp/cam_state/timestamp", limit=64).decode().strip())
        now = int(time.time())
        if state != "healthy" or now - timestamp not in range(0, 30):
            return False
        uptime = float(read_plain("/proc/uptime", limit=128).decode().split()[0])
        if snapshot.get("schema") != 1:
            return False
        if snapshot.get("boot_id") not in (None, boot):
            return False
        producer_ms = snapshot.get("observed_monotonic_ms")
        if type(producer_ms) not in (int, float):
            return False
        if not -1000 <= 1000 * uptime - producer_ms <= 5000:
            return False
        observed = snapshot.get("observations")
        if not isinstance(observed, list) or not observed:
            return False
        return all(isinstance(row, dict) and row.get("status") in ("OK", "N/A")
                   for row in observed)
    except (OSError, KeyError, ValueError, TypeError):
        return False

source_bytes = read_plain(CONFIG, owner=0)
ord_bytes = read_plain(ORD, owner=0)
runtime_bytes = read_plain(RUNTIME, owner=0)
source = json.loads(source_bytes)
runtime = json.loads(runtime_bytes)
channels = (("i2c2", "ch0"), ("i2c2", "ch1"),
            ("i2c1", "ch2"), ("i2c1", "ch3"))
led_source = tuple(source["VHL_CAM"][bus][ch]["led_flash"]["enable"]
                   for bus, ch in channels)
led_runtime = tuple(runtime["VHL_CAM"][bus][ch]["led_flash"]["enable"]
                    for bus, ch in channels)
if led_source != led_runtime:
    raise ValueError("MCP LED source and runtime diverged")

result = subprocess.run(
    ["/opt/pim/bin/cam-recoveryctl", "status", "--json"],
    capture_output=True, text=True, timeout=8, check=False)
if result.returncode:
    raise ValueError("camera owner status unavailable")
owner = json.loads(result.stdout)
idle = (owner.get("owner", {}).get("lifecycle") in ("ACTIVE", "DEGRADED")
        and owner.get("pending") is None and owner.get("active") is None)
procs, all_pids = process_pids()
binaries = {name: sha_executable(f"/proc/{pids[0]}/exe")
            for name, pids in procs.items() if len(pids) == 1}
binaries["/opt/pim/bin/chk_cam_operate.sh"] = digest(read_plain(
    "/opt/pim/bin/chk_cam_operate.sh", limit=4 * 1024 * 1024, owner=0))
snap = json.loads(read_plain(HEALTH))
boot = read_plain("/proc/sys/kernel/random/boot_id", limit=128).decode().strip()
state, phase, run_id, original_sha = journal()
print(json.dumps({
    "target": TARGET,
    "observed_at_epoch": int(time.time()),
    "journal_state": state, "journal_phase": phase,
    "journal_run_id": run_id, "journal_original_sha": original_sha,
    "edgeconf_sha": digest(source_bytes),
    "ord_conf_sha": digest(ord_bytes),
    "runtime_sha": digest(runtime_bytes),
    "led_flags": led_runtime,
    "binary_hashes": binaries,
    "consumers_ready": healthy_consumer(owner, procs, snap, boot),
    "writer_closed": no_writer_handles(all_pids),
    "owner_idle": idle,
}, sort_keys=True, separators=(",", ":")))
'''


class PimReadOnlyInspector:
    """One fixed source-owned read-only script through an existing SSH handle.

    Running its capture requires the real host's reviewed owner context.
    Testing uses a fake SSH handle. No user-supplied remote code or path
    can be passed to this method.
    """

    def __init__(self, ssh: object, *, source_sha: str) -> None:
        self.ssh = ssh
        self.source_sha = source_sha

    def observe(self) -> BoardSample:
        if not callable(getattr(self.ssh, "run", None)):
            raise AdapterBlocked("missing pinned SSH inspector transport")
        command = "python3 -B -c " + shlex.quote(REMOTE_PROBE)
        try:
            raw = self.ssh.run(command, retries=0)
            if type(raw) is not str or len(raw) > 16384:
                raise AdapterBlocked("read-only board snapshot missing")
            document = json.loads(raw)
            expected = {
                "target", "observed_at_epoch", "journal_state", "journal_phase",
                "journal_run_id", "journal_original_sha", "edgeconf_sha",
                "ord_conf_sha", "runtime_sha", "led_flags", "binary_hashes",
                "consumers_ready", "writer_closed", "owner_idle",
            }
            if not isinstance(document, dict) or set(document) != expected:
                raise AdapterBlocked("unexpected read-only board snapshot schema")
            if document["target"] != TARGET:
                raise AdapterBlocked("board target mismatch")
            if (type(document["observed_at_epoch"]) is not int
                    or type(document["led_flags"]) is not list
                    or len(document["led_flags"]) != 4
                    or any(type(flag) is not bool for flag in document["led_flags"])):
                raise AdapterBlocked("invalid board timestamp or LED flags")
            for field in ("consumers_ready", "writer_closed", "owner_idle"):
                if type(document[field]) is not bool:
                    raise AdapterBlocked("invalid board boolean proof")
            for field in ("edgeconf_sha", "ord_conf_sha", "runtime_sha"):
                val = document[field]
                if (type(val) is not str or len(val) != 64
                        or any(ch not in "0123456789abcdef" for ch in val)):
                    raise AdapterBlocked("invalid board SHA256 proof")
            hashes = document["binary_hashes"]
            if (not isinstance(hashes, dict) or set(hashes) != {
                "gstApp", "ord", "vcm", "/opt/pim/bin/chk_cam_operate.sh"
            } or any(type(v) is not str or len(v) != 64
                     or any(c not in "0123456789abcdef" for c in v)
                     for v in hashes.values())):
                raise AdapterBlocked("invalid installed binary manifest")
            state = document["journal_state"]
            if state == "ABSENT":
                if any(document[k] is not None for k in (
                    "journal_phase", "journal_run_id", "journal_original_sha"
                )):
                    raise AdapterBlocked("ambiguous absent journal")
            elif state == "DIRTY":
                if (document["journal_phase"] not in {
                        "JOURNALED", "APPLIED", "REBOOTED", "RESTORED",
                        "VERIFIED", "CLOSED"
                    } or type(document["journal_run_id"]) is not str
                        or not document["journal_run_id"]
                        or type(document["journal_original_sha"]) is not str
                        or len(document["journal_original_sha"]) != 64):
                    raise AdapterBlocked("invalid on-board journal manifest")
            else:
                raise AdapterBlocked("unknown on-board journal state")
            return BoardSample(
                target=document["target"], source_sha=self.source_sha,
                observed_at_epoch=document["observed_at_epoch"],
                journal_state=document["journal_state"],
                journal_phase=document["journal_phase"],
                journal_run_id=document["journal_run_id"],
                journal_original_sha=document["journal_original_sha"],
                edgeconf_sha=document["edgeconf_sha"],
                ord_conf_sha=document["ord_conf_sha"],
                runtime_sha=document["runtime_sha"],
                led_flags=tuple(document["led_flags"]),
                binary_hashes=document["binary_hashes"],
                consumers_ready=document["consumers_ready"],
                writer_closed=document["writer_closed"],
                owner_idle=document["owner_idle"])
        except AdapterBlocked:
            raise
        except Exception as exc:
            # ssh.py connection and timeout failures are not necessarily
            # subclasses of OSError. Any failed collection must BLOCK.
            raise AdapterBlocked("untrusted read-only board probe") from exc
