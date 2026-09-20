#!/usr/bin/env python3
"""Real-time process monitor for Kickzuru (or any PID)."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta

import psutil
from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

REFRESH_HZ = 2
DEFAULT_PROCESS_NAME = "Kickzuru"


def format_bytes(n: float) -> str:
    if n < 0:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def format_rate(n: float) -> str:
    return f"{format_bytes(n)}/s"


def format_duration(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))


def bar(value: float, width: int = 20, color: str = "green") -> Text:
    value = max(0.0, min(100.0, value))
    filled = int(value / 100 * width)
    empty = width - filled
    text = Text()
    text.append("█" * filled, style=color)
    text.append("░" * empty, style="dim")
    text.append(f" {value:5.1f}%")
    return text


def color_for_percent(value: float) -> str:
    if value >= 85:
        return "red"
    if value >= 60:
        return "yellow"
    return "green"


@dataclass
class Sample:
    cpu_percent: float
    mem_rss: int
    mem_vms: int
    mem_private: int | None
    mem_percent: float
    num_threads: int
    num_handles: int | None
    num_connections: int | None
    io_read_bytes: int
    io_write_bytes: int
    io_other_bytes: int
    io_read_rate: float
    io_write_rate: float
    ctx_switches: int | None
    status: str
    uptime: float


@dataclass
class TreeSample:
    process_count: int
    child_count: int
    cpu_percent: float
    mem_rss: int
    mem_private: int
    mem_percent: float
    num_threads: int
    num_handles: int
    io_read_bytes: int
    io_write_bytes: int
    io_read_rate: float
    io_write_rate: float


class ProcessMonitor:
    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.proc = psutil.Process(pid)
        self._prev_io: tuple[int, int] | None = None
        self._prev_io_time: float | None = None
        self._prev_tree_io: tuple[int, int] | None = None
        self._prev_tree_io_time: float | None = None
        # Prime CPU measurement (first call returns 0.0)
        self.proc.cpu_percent(interval=None)

    def _io_rates(
        self,
        read_b: int,
        write_b: int,
        now: float,
        prev_io: tuple[int, int] | None,
        prev_time: float | None,
    ) -> tuple[float, float, tuple[int, int], float]:
        read_rate = write_rate = 0.0
        if prev_io is not None and prev_time is not None:
            dt = now - prev_time
            if dt > 0:
                read_rate = (read_b - prev_io[0]) / dt
                write_rate = (write_b - prev_io[1]) / dt
        return read_rate, write_rate, (read_b, write_b), now

    def sample(self) -> Sample:
        now = time.monotonic()
        with self.proc.oneshot():
            cpu = self.proc.cpu_percent(interval=None)
            mem = self.proc.memory_info()
            mem_pct = self.proc.memory_percent()
            threads = self.proc.num_threads()
            handles = getattr(self.proc, "num_handles", lambda: None)()
            status = self.proc.status()
            create_time = self.proc.create_time()
            mem_private = getattr(mem, "private", None)
            try:
                io = self.proc.io_counters()
                read_b, write_b = io.read_bytes, io.write_bytes
                other_b = getattr(io, "other_bytes", 0) or 0
            except (psutil.AccessDenied, AttributeError):
                read_b, write_b, other_b = 0, 0, 0

            try:
                ctx = self.proc.num_ctx_switches()
                ctx_total = (ctx.voluntary or 0) + (ctx.involuntary or 0)
            except (psutil.AccessDenied, AttributeError):
                ctx_total = None

            try:
                num_connections = len(self.proc.net_connections())
            except (psutil.AccessDenied, AttributeError):
                num_connections = None

        read_rate, write_rate, self._prev_io, self._prev_io_time = self._io_rates(
            read_b, write_b, now, self._prev_io, self._prev_io_time
        )

        return Sample(
            cpu_percent=cpu,
            mem_rss=mem.rss,
            mem_vms=mem.vms,
            mem_private=mem_private,
            mem_percent=mem_pct,
            num_threads=threads,
            num_handles=handles,
            num_connections=num_connections,
            io_read_bytes=read_b,
            io_write_bytes=write_b,
            io_other_bytes=other_b,
            io_read_rate=read_rate,
            io_write_rate=write_rate,
            ctx_switches=ctx_total,
            status=status,
            uptime=time.time() - create_time,
        )

    def sample_tree(self) -> TreeSample:
        now = time.monotonic()
        try:
            procs = [self.proc, *self.proc.children(recursive=True)]
        except psutil.NoSuchProcess:
            procs = [self.proc]

        cpu = 0.0
        mem_rss = 0
        mem_private = 0
        mem_percent = 0.0
        threads = 0
        handles = 0
        read_b = 0
        write_b = 0
        child_count = max(0, len(procs) - 1)

        for proc in procs:
            try:
                cpu += proc.cpu_percent(interval=None)
                mem = proc.memory_info()
                mem_rss += mem.rss
                mem_private += getattr(mem, "private", mem.rss) or mem.rss
                mem_percent += proc.memory_percent()
                threads += proc.num_threads()
                handles += getattr(proc, "num_handles", lambda: 0)() or 0
                try:
                    io = proc.io_counters()
                    read_b += io.read_bytes
                    write_b += io.write_bytes
                except (psutil.AccessDenied, AttributeError):
                    pass
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        read_rate, write_rate, self._prev_tree_io, self._prev_tree_io_time = self._io_rates(
            read_b, write_b, now, self._prev_tree_io, self._prev_tree_io_time
        )

        return TreeSample(
            process_count=len(procs),
            child_count=child_count,
            cpu_percent=cpu,
            mem_rss=mem_rss,
            mem_private=mem_private,
            mem_percent=mem_percent,
            num_threads=threads,
            num_handles=handles,
            io_read_bytes=read_b,
            io_write_bytes=write_b,
            io_read_rate=read_rate,
            io_write_rate=write_rate,
        )


def metric_table(title_style: str = "cyan") -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1), expand=True)
    table.add_column("Metric", style=title_style, width=14)
    table.add_column("Value", style="white", overflow="fold")
    return table


def metric_value(text: str, percent: float | None = None) -> Text | str:
    if percent is None:
        return text
    line = Text()
    line.append(text)
    line.append("  ")
    line.append_text(bar(percent, width=12, color=color_for_percent(percent)))
    return line


def build_process_table(sample: Sample, proc: psutil.Process) -> Table:
    table = metric_table()

    table.add_row("CPU", metric_value(f"{sample.cpu_percent:.1f}%", sample.cpu_percent))
    table.add_row("Memory (RSS)", metric_value(format_bytes(sample.mem_rss), sample.mem_percent))
    table.add_row("Private Memory", format_bytes(sample.mem_private) if sample.mem_private is not None else "—")
    table.add_row("Virtual Memory", format_bytes(sample.mem_vms))
    table.add_row("Memory %", f"{sample.mem_percent:.2f}%")
    table.add_row("Threads", str(sample.num_threads))
    if sample.num_handles is not None:
        table.add_row("Handles", str(sample.num_handles))
    table.add_row("Status", sample.status)
    table.add_row("Uptime", format_duration(sample.uptime))
    if sample.num_connections is not None:
        table.add_row("Connections", str(sample.num_connections))
    table.add_row("Disk Read", format_bytes(sample.io_read_bytes))
    table.add_row("Disk Write", format_bytes(sample.io_write_bytes))
    if sample.io_other_bytes:
        table.add_row("Other I/O", format_bytes(sample.io_other_bytes))
    table.add_row("Read Rate", format_rate(sample.io_read_rate))
    table.add_row("Write Rate", format_rate(sample.io_write_rate))
    if sample.ctx_switches is not None:
        table.add_row("Context Switches", f"{sample.ctx_switches:,}")

    try:
        table.add_row("Executable", proc.exe())
    except (psutil.AccessDenied, psutil.NoSuchProcess):
        pass

    return table


def build_tree_table(tree: TreeSample) -> Table:
    table = metric_table("yellow")

    table.add_row("Processes", f"{tree.process_count} ({tree.child_count} children)")
    table.add_row("Total CPU", metric_value(f"{tree.cpu_percent:.1f}%", tree.cpu_percent))
    table.add_row("Total Memory", metric_value(format_bytes(tree.mem_rss), tree.mem_percent))
    table.add_row("Private Memory", format_bytes(tree.mem_private))
    table.add_row("Memory %", f"{tree.mem_percent:.2f}%")
    table.add_row("Total Threads", str(tree.num_threads))
    table.add_row("Total Handles", str(tree.num_handles))
    table.add_row("Disk Read", format_bytes(tree.io_read_bytes))
    table.add_row("Disk Write", format_bytes(tree.io_write_bytes))
    table.add_row("Read Rate", format_rate(tree.io_read_rate))
    table.add_row("Write Rate", format_rate(tree.io_write_rate))
    return table


def build_system_table() -> Table:
    vm = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=None)
    table = metric_table("magenta")

    table.add_row("System CPU", metric_value(f"{cpu:.1f}%", cpu))
    table.add_row(
        "System RAM",
        metric_value(f"{format_bytes(vm.used)} / {format_bytes(vm.total)}", vm.percent),
    )
    table.add_row("RAM Available", format_bytes(vm.available))
    table.add_row("RAM Used", f"{vm.percent:.1f}%")

    try:
        disk = psutil.disk_usage("/")
    except (OSError, PermissionError):
        disk = psutil.disk_usage("C:\\")

    table.add_row(
        "Disk Used",
        metric_value(f"{format_bytes(disk.used)} / {format_bytes(disk.total)}", disk.percent),
    )
    table.add_row("Disk Free", format_bytes(disk.free))

    return table


def build_children_table(proc: psutil.Process) -> Table | None:
    try:
        children = proc.children(recursive=True)
    except psutil.NoSuchProcess:
        return None

    if not children:
        return None

    for child in children:
        try:
            child.cpu_percent(interval=None)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    table = Table(title=f"Child Processes ({len(children)})", expand=True)
    table.add_column("PID", style="cyan", width=8)
    table.add_column("Name", width=22)
    table.add_column("CPU %", justify="right", width=8)
    table.add_column("Memory", justify="right", width=12)
    table.add_column("Threads", justify="right", width=8)
    table.add_column("Status", width=10)

    for child in children[:20]:
        try:
            table.add_row(
                str(child.pid),
                child.name(),
                f"{child.cpu_percent(interval=None):.1f}",
                format_bytes(child.memory_info().rss),
                str(child.num_threads()),
                child.status(),
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            table.add_row(str(child.pid), "—", "—", "—", "—", "—")

    if len(children) > 20:
        table.add_row("…", f"+{len(children) - 20} more", "", "", "", "")

    return table


def normalize_name(name: str) -> str:
    """Strip the Windows executable suffix so 'Kickzuru' matches 'Kickzuru.exe'."""
    name = name.lower()
    return name[:-4] if name.endswith(".exe") else name


def find_processes_by_name(name: str) -> list[tuple[int, str]]:
    target = normalize_name(name)
    matches: list[tuple[int, str]] = []
    for proc in psutil.process_iter(["pid", "name"]):
        proc_name = proc.info["name"]
        if proc_name and normalize_name(proc_name) == target:
            matches.append((proc.info["pid"], proc_name))
    matches.sort(key=lambda item: item[0])
    return matches


def print_process_list(name: str, matches: list[tuple[int, str]]) -> None:
    console = Console()
    if not matches:
        console.print(f"[red]No process found with name '{name}'.[/red]")
        console.print(f"Start {name} first, then run this monitor again.")
        console.print(f"Tip: run [cyan]python monitor.py --list[/cyan] to check.")
        return

    table = Table(title=f"Processes matching '{name}'")
    table.add_column("PID", style="cyan", justify="right")
    table.add_column("Name", style="white")
    for pid, proc_name in matches:
        table.add_row(str(pid), proc_name)
    console.print(table)
    if len(matches) > 1:
        console.print("[dim]Multiple matches found. Pass [cyan]--pid[/cyan] to pick one.[/dim]")


def resolve_pid(pid: int | None, name: str | None, console: Console) -> int:
    if pid is not None:
        return pid

    if not name:
        raise SystemExit("Pass --name to find a process automatically, or --pid to monitor a specific process.")

    matches = find_processes_by_name(name)
    if not matches:
        console.print(f"[red]No process found with name '{name}'.[/red]")
        console.print(f"Start {name} first, then run this monitor again.")
        console.print("Tip: run [cyan]python monitor.py --list[/cyan] to see matching processes.")
        raise SystemExit(1)

    if len(matches) > 1:
        console.print(f"[yellow]Found {len(matches)} processes named '{name}'. Using PID {matches[0][0]}.[/yellow]")
        console.print("[dim]Pass [cyan]--pid[/cyan] to monitor a different instance:[/dim]")
        for match_pid, match_name in matches:
            console.print(f"  [cyan]{match_pid}[/cyan]  {match_name}")

    return matches[0][0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Real-time Kickzuru process monitor")
    parser.add_argument("-p", "--pid", type=int, help="Process ID to monitor")
    parser.add_argument(
        "-n",
        "--name",
        default=DEFAULT_PROCESS_NAME,
        help=f"Process name to find when --pid is not set (default: {DEFAULT_PROCESS_NAME})",
    )
    parser.add_argument(
        "-l",
        "--list",
        action="store_true",
        help="List matching processes and exit (uses --name)",
    )
    parser.add_argument("-r", "--refresh", type=float, default=REFRESH_HZ, help="Refresh rate in Hz (default: 2)")
    args = parser.parse_args()

    console = Console()

    if args.list:
        matches = find_processes_by_name(args.name)
        print_process_list(args.name, matches)
        sys.exit(0 if matches else 1)

    try:
        pid = resolve_pid(args.pid, args.name if args.pid is None else None, console)
        proc = psutil.Process(pid)
    except psutil.NoSuchProcess:
        console.print(f"[red]Process {pid if 'pid' in dir() else args.pid or args.name} not found.[/red]")
        console.print("Make sure Kickzuru is running, or pass --pid explicitly.")
        sys.exit(1)

    monitor = ProcessMonitor(pid)
    interval = 1.0 / max(0.5, args.refresh)

    try:
        proc_name = proc.name()
    except psutil.NoSuchProcess:
        console.print("[red]Process exited before monitoring started.[/red]")
        sys.exit(1)

    header = f"[bold green]Kickzuru Monitor[/bold green]  PID [cyan]{pid}[/cyan]  [dim]{proc_name}[/dim]"

    with Live(console=console, refresh_per_second=args.refresh, screen=True) as live:
        while True:
            try:
                if not proc.is_running():
                    live.update(Panel("[red]Process has exited.[/red]", title="Kickzuru Monitor"))
                    time.sleep(1)
                    continue

                sample = monitor.sample()
                tree = monitor.sample_tree()
                children = build_children_table(proc)

                layout = Layout()
                layout.split_column(
                    Layout(name="top", ratio=3),
                    Layout(name="bottom", ratio=2),
                )
                layout["top"].split_column(
                    Layout(Panel(build_process_table(sample, proc), title="Process", border_style="green"), ratio=2),
                    Layout(name="summary", ratio=1),
                )
                layout["top"]["summary"].split_row(
                    Layout(Panel(build_tree_table(tree), title="Process Tree", border_style="yellow")),
                    Layout(Panel(build_system_table(), title="System", border_style="magenta")),
                )

                bottom_content: list = []
                if children:
                    bottom_content.append(children)

                timestamp = datetime.now().strftime("%H:%M:%S")
                footer = Text(f"Updated {timestamp}  ·  Ctrl+C to quit  ·  Refresh: {args.refresh:.1f} Hz", style="dim")
                bottom_content.append(footer)

                layout["bottom"].update(Panel(Group(*bottom_content), title="Details", border_style="blue"))
                live.update(Panel(layout, title=header, border_style="bright_blue"))

            except psutil.NoSuchProcess:
                live.update(Panel("[red]Process has exited.[/red]", title="Kickzuru Monitor"))
            except KeyboardInterrupt:
                break

            time.sleep(interval)


if __name__ == "__main__":
    main()
