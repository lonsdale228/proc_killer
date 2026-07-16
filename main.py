import psutil, sys, time, os, datetime

TARGET_NAMES = {"python.exe", "pythonw.exe"}
PROC_LIMIT = 6000 * 1024 * 1024  # 6 GB
RAM_CRIT_PCT = 97.0
RAM_CRIT_AVAIL = 256 * 1024 * 1024  # < 256 MB free
LOG_FILE = "watchdog.log"
POLL = 0.5
KILL_COOLDOWN = 5.0
KILL_TIMEOUT = 5

MY_PID = os.getpid()
IS_WIN = sys.platform == "win32"


def log(msg: str):
    line = f"[watchdog {datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def mem_of(p: psutil.Process) -> int:
    mi = p.memory_info()
    if IS_WIN:
        return max(getattr(mi, "private", 0), mi.rss)
    return mi.rss


def proc_info(p: psutil.Process, mem: int) -> str:
    try:
        cmd = " ".join(p.cmdline()[1:3])
    except psutil.Error:
        cmd = "?"
    return f"pid={p.pid} mem={mem / 1e6:.0f}MB cmd={cmd}"


def get_targets():
    out = []
    for p in psutil.process_iter(["name"]):
        try:
            if p.pid == MY_PID:
                continue
            name = (p.info["name"] or "").lower()
            if name not in TARGET_NAMES and name != "python" and name != "python3":
                continue
            out.append((p, mem_of(p)))
        except psutil.Error:
            continue
    return out


def kill(p: psutil.Process, mem: int, reason: str) -> bool:
    info = proc_info(p, mem)
    try:
        p.kill()
        psutil.wait_procs([p], timeout=KILL_TIMEOUT)
        log(f"KILLED ({reason}): {info}")
        return True
    except psutil.NoSuchProcess:
        return True
    except psutil.Error as e:
        log(f"failed to kill {info}: {e}")
        return False


def log_top_ram(n: int = 5):
    procs = []
    for p in psutil.process_iter(["name", "memory_info"]):
        mi = p.info["memory_info"]
        if mi is not None:
            procs.append((mi.rss, p.info["name"] or "?", p.pid))
    procs.sort(reverse=True)
    top = ", ".join(f"{name} pid={pid} {rss / 1e6:.0f}MB" for rss, name, pid in procs[:n])
    log(f"top{n} RAM: {top}")


def ram_critical():
    vm = psutil.virtual_memory()
    if vm.percent >= RAM_CRIT_PCT:
        return f"RAM {vm.percent:.1f}% >= {RAM_CRIT_PCT}%"
    if vm.available <= RAM_CRIT_AVAIL:
        return f"only {vm.available / 1e6:.0f} MB RAM available"
    return None


def main():
    log(f"watchdog started, pid={MY_PID}, targets={sorted(TARGET_NAMES)}, "
        f"proc limit={PROC_LIMIT / 1e6:.0f}MB, RAM crit={RAM_CRIT_PCT}%")
    last_kill = 0.0

    while True:
        in_cooldown = (time.monotonic() - last_kill) < KILL_COOLDOWN
        targets = get_targets()
        killed = False

        if not in_cooldown:
            for p, mem in targets:
                if mem > PROC_LIMIT:
                    if kill(p, mem, f"process {mem / 1e6:.0f}MB > limit {PROC_LIMIT / 1e6:.0f}MB"):
                        killed = True
                        break

            if not killed:
                reason = ram_critical()
                if reason:
                    log(f"CRITICAL: {reason}")
                    log_top_ram()
                    fresh = []
                    for p, _ in targets:
                        try:
                            fresh.append((p, mem_of(p)))
                        except psutil.Error:
                            continue
                    if fresh:
                        p, mem = max(fresh, key=lambda t: t[1])
                        killed = kill(p, mem, reason)
                    else:
                        log("no python processes to kill")

        elif ram_critical() or any(m > PROC_LIMIT for _, m in targets):
            log(f"still critical, waiting cooldown "
                f"{KILL_COOLDOWN - (time.monotonic() - last_kill):.1f}s")

        if killed:
            last_kill = time.monotonic()

        time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log("watchdog stopped")
