#!/usr/bin/env python3
"""
OUO Link Resolver - Terminal User Interface
Made by DARK SHADOW
Real-time TUI with subprocess output parsing
"""
import sys
import os
import time
import threading
import subprocess
import re
import signal
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.live import Live
from rich.text import Text
from rich.align import Align
from rich.box import ROUNDED, DOUBLE, HEAVY
from rich.prompt import IntPrompt

# Add script dir to path
SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)

console = Console()


class OUOTUI:
    """Terminal UI for OUO Link Resolver - Real-time subprocess monitoring"""

    def __init__(self):
        # State
        self.running = True
        self.proxy_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.expired_count = 0
        self.current_round = 0
        self.total_rounds = 0
        self.status = "Idle"
        self.quiet_mode = False
        self.logs = []
        self.max_logs = 20
        self.total_links = 0
        self.processed_links = 0
        self.last_result = ""

        # Settings (passed to the resolver subprocess)
        self.proxy_mode = "direct"      # direct | public | file
        self.proxy_file = "proxies.txt"
        self.device = "auto"            # auto/android/ios/mobile/desktop
        self.timeout = 12
        self.clean = False
        self.output_file = "impressions.txt"

        # Thread communication
        self.menu_choice = None
        self.rounds_input = None
        self.worker_proc = None
        self.worker_thread = None

    # ---------- Logging ----------
    def add_log(self, msg: str, level: str = "info"):
        """Add timestamped log entry with level styling"""
        timestamp = time.strftime("%H:%M:%S")
        style_map = {
            "info": "dim white",
            "success": "bright_green",
            "warning": "bright_yellow",
            "error": "bright_red",
            "cyan": "bright_cyan",
        }
        style = style_map.get(level, "white")
        self.logs.append(f"[{timestamp}] {msg}")
        if len(self.logs) > self.max_logs:
            self.logs.pop(0)

    # ---------- Layout Components ----------
    def make_header(self) -> Panel:
        """Top banner: OUO + made by DARK SHADOW"""
        title = Text()
        title.append("OUO", style="bold bright_cyan on black")
        title.append("  ", style="on black")
        title.append("made by DARK SHADOW", style="bright_yellow on black")
        return Panel(
            Align.center(title),
            box=DOUBLE,
            border_style="bright_cyan",
            style="on black",
            padding=(0, 2),
        )

    def make_stats_table(self) -> Panel:
        """Left panel: live metrics table"""
        table = Table(box=HEAVY, border_style="bright_blue", show_header=False, pad_edge=False)
        table.add_column("Metric", style="bold bright_white", width=18)
        table.add_column("Value", style="bold bright_green", width=12)

        table.add_row("🌐 Proxies", str(self.proxy_count))
        table.add_row("✅ Success", str(self.success_count))
        table.add_row("❌ Failed", str(self.fail_count))
        table.add_row("⚠️ Expired", str(self.expired_count))
        table.add_row("📦 Progress", f"{self.processed_links}/{self.total_links}")
        table.add_row("🔄 Round", f"{self.current_round}/{self.total_rounds or '∞'}")
        table.add_row("🛡️ Proxy", self.proxy_mode)
        table.add_row("📊 Status", self.status)
        table.add_row("🔇 Quiet", "ON" if self.quiet_mode else "OFF")

        return Panel(
            table,
            title="[bold bright_white]LIVE STATS[/bold bright_white]",
            border_style="bright_blue",
            box=ROUNDED,
            padding=(0, 1),
        )

    def make_logs_panel(self) -> Panel:
        """Right panel: timestamped logs"""
        log_text = Text()
        for log in self.logs:
            log_text.append(log + "\n", style="dim white")
        if not self.logs:
            log_text.append("Waiting for activity...", style="dim italic")

        return Panel(
            log_text,
            title="[bold bright_white]LOGS[/bold bright_white]",
            border_style="bright_magenta",
            box=ROUNDED,
            padding=(0, 1),
        )

    def make_menu(self) -> Panel:
        """Footer: numbered menu"""
        menu = Table(box=None, show_header=False, pad_edge=False, expand=True)
        menu.add_column("Key", style="bold bright_cyan", width=4, justify="center")
        menu.add_column("Action", style="bright_white", width=30)

        menu.add_row("1", "Run Rounds (input count)")
        menu.add_row("2", "Scrape Proxies")
        menu.add_row("3", "Other Functions")
        menu.add_row("4", "Toggle Quiet Mode")
        menu.add_row("5", "Settings")
        menu.add_row("S", "Stop current task")
        menu.add_row("Q", "Quit")

        return Panel(
            menu,
            title="[bold bright_white]MENU[/bold bright_white]",
            border_style="bright_green",
            box=ROUNDED,
            padding=(0, 1),
        )

    def make_layout(self) -> Layout:
        """Compose full layout"""
        layout = Layout()
        layout.split_column(
            Layout(name="header", size=5),
            Layout(name="body"),
            Layout(name="menu", size=12),
        )
        layout["body"].split_row(
            Layout(name="stats", size=45),
            Layout(name="logs"),
        )

        layout["header"].update(self.make_header())
        layout["stats"].update(self.make_stats_table())
        layout["logs"].update(self.make_logs_panel())
        layout["menu"].update(self.make_menu())

        return layout

    # ---------- Output Parsing ----------
    def parse_output_line(self, line: str):
        """Parse CLI output line and update stats in real-time"""
        line = line.strip()
        if not line:
            return

        # Add to logs (non-quiet mode)
        if not self.quiet_mode:
            if "✅" in line or "SUCCESS" in line or "👉 Final Destination" in line:
                self.add_log(line, "success")
            elif "❌" in line or "FAILED" in line or "Could not resolve" in line or "Error" in line:
                self.add_log(line, "error")
            elif "⚠️" in line or "EXPIRED" in line or "Proxy Discarded" in line or "Proxy failed" in line:
                self.add_log(line, "warning")
            elif "🌐" in line or "🛡️" in line or "🔄 ROUND" in line or "Scraping" in line:
                self.add_log(line, "cyan")
            else:
                self.add_log(line, "info")

        # Parse proxy count
        proxy_match = re.search(r"Dynamic Proxy Pool:\s*(\d+)\s*active", line)
        if proxy_match:
            self.proxy_count = int(proxy_match.group(1))

        # Parse total links
        links_match = re.search(r"Total Links:\s*(\d+)", line)
        if links_match:
            self.total_links = int(links_match.group(1))

        # Parse round start
        round_match = re.search(r"🔄 ROUND\s+(\d+)", line)
        if round_match:
            self.current_round = int(round_match.group(1))
            self.status = f"Round {self.current_round} running..."

        # Round complete: only update status/round. Counters come from the
        # per-link lines below, so nothing is double-counted.
        complete_match = re.search(r"ROUND\s+(\d+)\s+COMPLETE.*?Success:\s*(\d+)/(\d+)", line)
        if complete_match:
            self.current_round = int(complete_match.group(1))
            self.status = f"Round {self.current_round} complete"

        # Live per-link progress (each link prints exactly one of these).
        if "👉 Final Destination:" in line:
            self.success_count += 1
            self.processed_links += 1
            self.last_result = line.split("👉 Final Destination:", 1)[-1].strip()
        elif "❌ Could not resolve destination" in line:
            self.fail_count += 1
            self.processed_links += 1
        elif "Status: EXPIRED / DELETED" in line:
            self.expired_count += 1
            self.processed_links += 1

        # Parse proxy pool size updates
        pool_match = re.search(r"Active proxies remaining in pool:\s*(\d+)", line)
        if pool_match:
            self.proxy_count = int(pool_match.group(1))

        # Parse scraping results
        if "Successfully harvested and saved" in line and "proxies into" in line:
            num_match = re.search(r"saved\s+(\d+)\s+proxies", line)
            if num_match:
                self.proxy_count = int(num_match.group(1))

        # Parse overall summary
        if "OVERALL AUTOMATION SESSION SUMMARY" in line:
            self.status = "Complete"

    # ---------- Process control ----------
    def stop_worker(self):
        """Stop the worker subprocess AND its Chromium children (process group),
        so no headless browsers are left running."""
        p = self.worker_proc
        if not p:
            return
        try:
            os.killpg(os.getpgid(p.pid), signal.SIGTERM)
        except Exception:
            try:
                p.terminate()
            except Exception:
                pass

    # ---------- Background Workers ----------
    def run_resolver_subprocess(self, rounds: int, concurrency: int = 2):
        """Worker: run CLI as subprocess and capture output in real-time"""
        self.total_rounds = rounds
        self.current_round = 0
        self.success_count = 0
        self.fail_count = 0
        self.expired_count = 0
        self.processed_links = 0
        self.last_result = ""
        self.status = "Starting..."
        self.add_log(f"Starting resolver: {rounds} rounds, concurrency={concurrency}", "success")

        try:
            # Build command (-u = unbuffered so logs stream in real-time)
            cmd = [sys.executable, "-u", "main.py", "-c", str(concurrency)]
            if rounds > 1:
                cmd.extend(["--rounds", str(rounds)])
            if self.proxy_mode == "public":
                cmd.append("-P")
            elif self.proxy_mode == "file":
                cmd.extend(["--proxy-file", self.proxy_file])
            cmd.extend(["--device", self.device, "--timeout", str(self.timeout)])
            if self.output_file:
                cmd.extend(["-o", self.output_file])
            if self.clean:
                cmd.append("--clean")

            self.worker_proc = subprocess.Popen(
                cmd,
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                start_new_session=True,   # own process group -> clean stop
            )

            # Read output line by line in real-time
            if self.worker_proc and self.worker_proc.stdout:
                for line in iter(self.worker_proc.stdout.readline, ''):
                    if not self.running:
                        self.stop_worker()
                        break
                    self.parse_output_line(line)

            rc = self.worker_proc.wait()
            if self.running:
                if rc == 0:
                    self.status = "Complete"
                    self.add_log("Resolver finished", "success")
                else:
                    self.status = f"Failed (exit {rc})"
                    self.add_log(f"Resolver failed with exit code {rc}", "error")

        except Exception as e:
            self.status = "Error"
            self.add_log(f"Error: {e}", "error")
        finally:
            self.worker_proc = None

    def scrape_proxies_subprocess(self):
        """Worker: fetch proxies via subprocess"""
        self.status = "Scraping proxies..."
        self.add_log("Scraping proxies from hide.mn...", "warning")

        try:
            cmd = [sys.executable, "-u", "main.py", "--fetch-proxies"]
            self.worker_proc = subprocess.Popen(
                cmd,
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
                start_new_session=True,
            )

            if self.worker_proc and self.worker_proc.stdout:
                for line in iter(self.worker_proc.stdout.readline, ''):
                    if not self.running:
                        self.stop_worker()
                        break
                    self.parse_output_line(line)

            rc = self.worker_proc.wait()
            if self.running:
                if rc == 0:
                    self.status = "Idle"
                    self.add_log(f"Scraping done. Proxies: {self.proxy_count}", "success")
                else:
                    self.status = f"Scrape failed (exit {rc})"
                    self.add_log(f"Scraping failed with exit code {rc}", "error")

        except Exception as e:
            self.add_log(f"Scrape error: {e}", "error")
            self.status = "Idle"
        finally:
            self.worker_proc = None

    def show_other_functions(self):
        """Display other CLI flags in logs"""
        self.add_log("Other functions available:", "cyan")
        self.add_log("  --clean       : Auto-prune dead links", "info")
        self.add_log("  --proxy-file  : Use custom proxy list", "info")
        self.add_log("  --device      : android/ios/desktop", "info")
        self.add_log("  --dwell SEC   : Post-destination dwell", "info")
        self.add_log("  -o FILE       : Custom output file", "info")
        self.add_log("  --timeout SEC : Page load timeout", "info")
        self.add_log("  --loop        : Infinite loop mode", "info")
        self.add_log("  --delay SEC   : Delay between rounds", "info")

    def toggle_quiet(self):
        self.quiet_mode = not self.quiet_mode
        self.add_log(f"Quiet mode: {'ON' if self.quiet_mode else 'OFF'}", "warning")

    # ---------- Settings ----------
    def settings_wizard(self):
        """Interactive settings (Enter keeps the current value). Runs in the
        single input thread, so it never races the menu reader."""
        console.print("[bold cyan]Settings (Enter = keep current)[/bold cyan]")

        mode = console.input(f"Proxy mode [direct/public/file] ({self.proxy_mode}): ").strip().lower()
        if mode in ("direct", "public", "file"):
            self.proxy_mode = mode
        if self.proxy_mode == "file":
            pf = console.input(f"Proxy file ({self.proxy_file}): ").strip()
            if pf:
                self.proxy_file = pf

        dev = console.input(f"Device [auto/android/ios/mobile/desktop] ({self.device}): ").strip().lower()
        if dev in ("auto", "android", "ios", "mobile", "desktop"):
            self.device = dev

        to = console.input(f"Timeout sec ({self.timeout}): ").strip()
        if to.isdigit():
            self.timeout = max(1, int(to))

        of = console.input(f"Output file ({self.output_file}): ").strip()
        if of:
            self.output_file = of

        cl = console.input(f"Prune expired links from input? [y/n] ({'y' if self.clean else 'n'}): ").strip().lower()
        if cl in ("y", "yes"):
            self.clean = True
        elif cl in ("n", "no"):
            self.clean = False

        self.add_log(
            f"Settings: proxy={self.proxy_mode}, device={self.device}, "
            f"timeout={self.timeout}, clean={self.clean}, out={self.output_file}",
            "success",
        )

    # ---------- Input Threads ----------
    def input_thread(self):
        """Single stdin reader: menu choice AND all prompts happen here, so
        two threads can never race for the same input line."""
        while self.running:
            try:
                choice = console.input("\n[bold bright_cyan]Select (1-5, S, Q):[/bold bright_cyan] ").strip().upper()
            except (EOFError, KeyboardInterrupt):
                self.menu_choice = "Q"
                break
            except Exception:
                time.sleep(0.1)
                continue

            if choice == "1":
                try:
                    rounds = IntPrompt.ask("[bold]How many rounds?[/bold]", default=1)
                    if rounds > 0:
                        concurrency = IntPrompt.ask("[bold]Concurrency (1-4)?[/bold]", default=2)
                        concurrency = max(1, min(concurrency, 4))
                        self.rounds_input = (rounds, concurrency)
                        self.menu_choice = "1"
                except (EOFError, KeyboardInterrupt):
                    self.menu_choice = "Q"
                    break
                except Exception:
                    self.rounds_input = None
                continue

            if choice == "5":
                try:
                    self.settings_wizard()
                except (EOFError, KeyboardInterrupt):
                    self.menu_choice = "Q"
                    break
                except Exception:
                    pass
                continue

            self.menu_choice = choice

    # ---------- Main Loop ----------
    def handle_choice(self, choice: str):
        """Process menu selection"""
        if choice == "1":
            self.stop_worker()
            # Rounds/concurrency were already collected by input_thread.
            if self.rounds_input:
                rounds, concurrency = self.rounds_input
                self.worker_thread = threading.Thread(
                    target=self.run_resolver_subprocess,
                    args=(rounds, concurrency),
                    daemon=True
                )
                self.worker_thread.start()
            self.rounds_input = None

        elif choice == "2":
            self.stop_worker()
            threading.Thread(target=self.scrape_proxies_subprocess, daemon=True).start()

        elif choice == "3":
            self.show_other_functions()

        elif choice == "4":
            self.toggle_quiet()

        elif choice == "S":
            self.stop_worker()
            self.status = "Stopped"
            self.add_log("Stopped by user", "warning")

        elif choice == "Q":
            self.stop_worker()
            self.running = False

    def run(self):
        """Main TUI loop"""
        self.add_log("OUO Link Resolver TUI started", "success")
        self.add_log("Press 1-5, S or Q", "info")

        # Start input thread
        threading.Thread(target=self.input_thread, daemon=True).start()

        with Live(self.make_layout(), console=console, refresh_per_second=4, screen=True) as live:
            while self.running:
                live.update(self.make_layout())

                if self.menu_choice:
                    choice = self.menu_choice
                    self.menu_choice = None
                    self.handle_choice(choice)

                time.sleep(0.1)

        console.clear()
        console.print("[bold bright_cyan]OUO[/bold bright_cyan] [bright_yellow]Goodbye![/bright_yellow]")


def main():
    console.clear()
    tui = OUOTUI()
    tui.run()


if __name__ == "__main__":
    main()
