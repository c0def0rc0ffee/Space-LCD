"""
<summary>
Collects the numbers shown on the screen: CPU temperature and load, GPU temperature and
load, memory use and the time.
</summary>
<remarks>
Any reading that is not available comes back as None rather than raising, so one missing
sensor blanks one figure instead of stopping the display. The pump speed is not read
because this machine's board does not expose the CPU fan header to Linux.
</remarks>
"""

import os
import platform
import re
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple

import psutil

CPU_SENSOR_PREFERENCE: List[Tuple[str, str]] = [
    ("k10temp", "Tctl"),
    ("k10temp", "Tdie"),
    ("zenpower", "Tdie"),
    ("coretemp", "Package id 0"),
]
"""
<summary>
Chip and label pairs tried in order for the CPU temperature. The first is what AMD Ryzen
boards report and what this machine has; the others cover Intel and older AMD kernels.
</summary>
"""


NET_HISTORY = 60
"""<summary>How many network readings the graph keeps: two minutes at the default 2 s interval.</summary>"""


@dataclass(frozen=True)
class SystemInfo:
    """
    <summary>The parts of the system that do not change while the service runs, read once.</summary>
    <remarks>Names are already shortened for a small round screen; see short_cpu_name() and short_gpu_name().</remarks>
    """

    cpu_name: Optional[str] = None
    cpu_cores: Optional[int] = None
    cpu_threads: Optional[int] = None
    gpu_name: Optional[str] = None
    gpu_memory_gb: Optional[float] = None
    kernel: Optional[str] = None


@dataclass
class Stats:
    """
    <summary>One snapshot of everything the screen shows.</summary>
    <remarks>
    Temperatures are degrees Celsius, loads are percentages, memory and disk are GB,
    network speeds are bits a second (the unit broadband is sold in), uptime is seconds.
    net_history holds (down, up) pairs, oldest first, covering net_span_s seconds.
    running holds lowercase names of running processes; steam_app is a running Steam game's id.
    coolant_temp, pump_rpm and fan_rpm come from a cooler that reports them (read_cooler()).
    </remarks>
    """

    cpu_temp: Optional[float] = None
    cpu_load: Optional[float] = None
    gpu_temp: Optional[float] = None
    gpu_load: Optional[float] = None
    ram_used_gb: Optional[float] = None
    ram_total_gb: Optional[float] = None
    now: datetime = field(default_factory=datetime.now)
    net_down: Optional[float] = None
    net_up: Optional[float] = None
    net_history: Tuple[Tuple[float, float], ...] = ()
    net_span_s: Optional[float] = None
    disk_used_gb: Optional[float] = None
    disk_total_gb: Optional[float] = None
    uptime_s: Optional[float] = None
    system: SystemInfo = field(default_factory=SystemInfo)
    running: frozenset = frozenset()
    steam_app: Optional[int] = None
    coolant_temp: Optional[float] = None
    pump_rpm: Optional[float] = None
    fan_rpm: Optional[float] = None


COOLER_HWMON = ("kraken2023", "x53", "z53")
"""
<summary>
hwmon names the nzxt-kraken3 kernel driver gives NZXT Kraken coolers. Their labels are
"Coolant temp", "Pump speed" and "Fan speed" (temp1, fan1, fan2).
</summary>
"""


def read_cooler(root: Path = Path("/sys/class/hwmon")) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    <summary>Coolant temperature (degrees C), pump and fan speed (rpm) from a Kraken's kernel driver.</summary>
    <param name="root">The hwmon class folder; tests pass a fake one.</param>
    <returns>The three readings, each None when missing. All None when no Kraken driver is loaded.</returns>
    <remarks>
    Reads sysfs files only. The kernel driver fills them from reports the cooler sends on
    its own, so this sends nothing to the cooler. A fan speed of 0 is reported as 0, not
    None: it can mean no fan is plugged into the pump's header.
    </remarks>
    """
    for chip in sorted(root.glob("hwmon*")):
        try:
            if (chip / "name").read_text().strip() not in COOLER_HWMON:
                continue
        except OSError:
            continue

        def value(name: str, scale: float) -> Optional[float]:
            """<summary>One sysfs reading divided by scale, or None when it cannot be read.</summary>"""
            try:
                return int((chip / name).read_text().strip()) / scale
            except (OSError, ValueError):
                return None

        return value("temp1_input", 1000.0), value("fan1_input", 1.0), value("fan2_input", 1.0)
    return None, None, None


def short_cpu_name(name: str) -> str:
    """
    <summary>Trims a CPU model string to what matters, e.g. "AMD Ryzen 7 5800X 8-Core Processor" to "Ryzen 7 5800X".</summary>
    <remarks>Drops the vendor, core count wording, "Processor", "CPU", clock speed and trademark marks.</remarks>
    """
    name = re.sub(r"\((R|TM)\)|®|™", "", name)
    name = re.sub(r"\b(AMD|Intel|Core\(TM\)|Processor|CPU)\b", "", name)
    name = re.sub(r"\b\d+-Core\b|@\s*[\d.]+\s*GHz", "", name, flags=re.IGNORECASE)
    return " ".join(name.split())


def short_gpu_name(name: str) -> str:
    """<summary>Trims a GPU name, e.g. "NVIDIA GeForce RTX 4070 Super" to "RTX 4070 Super".</summary>"""
    return " ".join(re.sub(r"\b(NVIDIA|GeForce|AMD|Radeon\(TM\)|Graphics)\b", "", name).split())


def read_system_info(nvidia_smi: Optional[str]) -> SystemInfo:
    """
    <summary>Reads the fixed system details once: CPU model and counts, GPU name and memory, kernel.</summary>
    <param name="nvidia_smi">Path to nvidia-smi, or None when there is no NVIDIA driver.</param>
    <remarks>Every part is optional; anything unreadable is left as None. The host name is deliberately not read.</remarks>
    """
    cpu_name = None
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("model name"):
                    cpu_name = short_cpu_name(line.split(":", 1)[1])
                    break
    except OSError:
        pass
    gpu_name, gpu_memory = None, None
    if nvidia_smi:
        try:
            result = subprocess.run([nvidia_smi, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                                    capture_output=True, text=True, timeout=5, check=False)
            first = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
            if first:
                name, _, memory = first.rpartition(",")
                gpu_name = short_gpu_name(name)
                gpu_memory = float(memory) / 1024 if memory.strip().replace(".", "").isdigit() else None
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass
    return SystemInfo(cpu_name=cpu_name, cpu_cores=psutil.cpu_count(logical=False),
                      cpu_threads=psutil.cpu_count(logical=True), gpu_name=gpu_name,
                      gpu_memory_gb=gpu_memory, kernel=platform.release() or None)


SKIP_ENV = frozenset({"steam", "steamwebhelper", "steam-runtime-launcher-service", "srt-bwrap", "pv-adverb"})
"""<summary>Steam's own processes, whose environment can carry an app id without a game running.</summary>"""


def read_processes() -> Tuple[frozenset, Optional[int]]:
    """
    <summary>Lowercase names of running processes, and a running Steam game's id if there is one.</summary>
    <remarks>
    One pass over the process table. Steam's game id comes from the launcher command line
    or, failing that, from the SteamAppId variable Steam puts in every game's environment
    (only this user's processes are read). Processes that end mid scan are skipped. Names
    and environments are compared, never stored or logged.
    </remarks>
    """
    from .rules import steam_game, steam_game_from_env
    names = set()
    cmdlines = []
    environments = []
    me = os.getuid()
    for process in psutil.process_iter(["name", "cmdline", "uids"]):
        try:
            info = process.info
            if info.get("name"):
                names.add(info["name"].lower())
            if info.get("cmdline") and "SteamLaunch" in info["cmdline"]:
                cmdlines.append(info["cmdline"])
            if info.get("uids") and info["uids"].real == me and info["name"] not in SKIP_ENV:
                env = process.environ()
                if "SteamAppId" in env or "SteamGameId" in env:
                    environments.append(env)
        except (psutil.Error, OSError):
            continue
    return frozenset(names), steam_game(cmdlines) or steam_game_from_env(environments)


class NetworkMeter:
    """
    <summary>Turns the system's byte counters into speeds and keeps a short history for the graph.</summary>
    <remarks>
    Counts every interface except loopback, so a VPN on top of the network card counts
    traffic twice; the trade is that nothing has to be configured. A counter that goes
    backwards (an interface going away) gives a speed of 0 for that reading.
    </remarks>
    """

    def __init__(self) -> None:
        """<summary>Takes the first counter reading; speeds start with the next one.</summary>"""
        self._last: Optional[Tuple[float, int, int]] = None
        self.history: Deque[Tuple[float, float]] = deque(maxlen=NET_HISTORY)
        self.times: Deque[float] = deque(maxlen=NET_HISTORY)
        self.sample(time.monotonic(), *self._counters())

    @staticmethod
    def _counters() -> Tuple[int, int]:
        """<summary>Total bytes received and sent on every interface but loopback.</summary>"""
        received = sent = 0
        for name, counters in psutil.net_io_counters(pernic=True).items():
            if name == "lo":
                continue
            received += counters.bytes_recv
            sent += counters.bytes_sent
        return received, sent

    def sample(self, now: float, received: int, sent: int) -> Tuple[Optional[float], Optional[float]]:
        """
        <summary>Records one counter reading and returns the speeds since the previous one.</summary>
        <param name="now">A monotonic time in seconds.</param>
        <returns>Download and upload in bits a second, or (None, None) on the first reading.</returns>
        """
        last, self._last = self._last, (now, received, sent)
        if last is None or now <= last[0]:
            return None, None
        seconds = now - last[0]
        down = max(0, received - last[1]) * 8 / seconds
        up = max(0, sent - last[2]) * 8 / seconds
        self.history.append((down, up))
        self.times.append(now)
        return down, up

    def span(self) -> Optional[float]:
        """<summary>Seconds between the oldest and newest reading in the history, or None with fewer than two.</summary>"""
        return self.times[-1] - self.times[0] if len(self.times) >= 2 else None

    def read(self) -> Tuple[Optional[float], Optional[float]]:
        """<summary>Takes a reading now and returns the speeds since the last one.</summary>"""
        return self.sample(time.monotonic(), *self._counters())


def pick_cpu_temp(readings: Dict[str, list]) -> Optional[float]:
    """
    <summary>
    Chooses the CPU temperature from psutil.sensors_temperatures() output.
    </summary>
    <param name="readings">Chip name mapped to a list of entries with label and current.</param>
    <returns>The preferred reading, or the first k10temp or coretemp entry, or None.</returns>
    """
    for chip, label in CPU_SENSOR_PREFERENCE:
        for entry in readings.get(chip, []):
            if entry.label == label:
                return float(entry.current)
    for chip in ("k10temp", "zenpower", "coretemp"):
        if readings.get(chip):
            return float(readings[chip][0].current)
    return None


def parse_nvidia_smi(output: str) -> Tuple[Optional[float], Optional[float]]:
    """
    <summary>
    Reads temperature and load from one line of nvidia-smi CSV output.
    </summary>
    <param name="output">Text such as "48, 9" from
    --query-gpu=temperature.gpu,utilization.gpu --format=csv,noheader,nounits.</param>
    <returns>Temperature and load, each None when missing or not a number.</returns>
    <remarks>Only the first GPU is used when several lines come back.</remarks>
    """
    lines = [line for line in output.strip().splitlines() if line.strip()]
    if not lines:
        return None, None
    parts = [part.strip() for part in lines[0].split(",")]

    def number(index: int) -> Optional[float]:
        """<summary>Converts one field, giving None for "[N/A]" and similar.</summary>"""
        try:
            return float(parts[index])
        except (IndexError, ValueError):
            return None

    return number(0), number(1)


class SensorReader:
    """
    <summary>Reads a fresh Stats snapshot each time read() is called.</summary>
    <remarks>
    Create one and keep it. psutil works out CPU load as the change since the previous
    call, so the very first reading from a new reader is always 0 per cent.
    </remarks>
    """

    def __init__(self) -> None:
        """<summary>Primes the CPU load and network counters, finds nvidia-smi, and reads the fixed system details once.</summary>"""
        psutil.cpu_percent(interval=None)
        self._nvidia_smi = shutil.which("nvidia-smi")
        self._system = read_system_info(self._nvidia_smi)
        self._network = NetworkMeter()

    def read(self) -> Stats:
        """
        <summary>Takes one snapshot. Never raises for a missing sensor.</summary>
        <returns>The current stats, with None for anything that could not be read.</returns>
        """
        stats = Stats()
        try:
            stats.cpu_temp = pick_cpu_temp(psutil.sensors_temperatures())
        except (AttributeError, OSError):
            stats.cpu_temp = None
        stats.cpu_load = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        stats.ram_total_gb = memory.total / 1024 ** 3
        stats.ram_used_gb = (memory.total - memory.available) / 1024 ** 3
        stats.gpu_temp, stats.gpu_load = self._read_gpu()
        stats.net_down, stats.net_up = self._network.read()
        stats.net_history = tuple(self._network.history)
        stats.net_span_s = self._network.span()
        try:
            disk = psutil.disk_usage("/")
            stats.disk_used_gb, stats.disk_total_gb = disk.used / 1024 ** 3, disk.total / 1024 ** 3
        except OSError:
            pass
        stats.uptime_s = time.time() - psutil.boot_time()
        stats.system = self._system
        stats.running, stats.steam_app = read_processes()
        stats.coolant_temp, stats.pump_rpm, stats.fan_rpm = read_cooler()
        return stats

    def _read_gpu(self) -> Tuple[Optional[float], Optional[float]]:
        """
        <summary>Asks nvidia-smi for the GPU temperature and load.</summary>
        <returns>Both values, or None for both when there is no NVIDIA driver or it hangs.</returns>
        <remarks>Capped at two seconds so a stuck driver cannot freeze the screen updates.</remarks>
        """
        if not self._nvidia_smi:
            return None, None
        try:
            result = subprocess.run(
                [self._nvidia_smi, "--query-gpu=temperature.gpu,utilization.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=2, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None, None
        if result.returncode != 0:
            return None, None
        return parse_nvidia_smi(result.stdout)
