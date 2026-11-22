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
        table.add_row("🔗 Total Links", str(self.total_links))
        table.add_row("📦 Processed", str(self.processed_links))
        table.add_row("🔄 Round", f"{self.current_round}/{self.total_rounds or '∞'}")
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
            # Determine log level from content
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

        # Parse round complete with stats
        complete_match = re.search(r"ROUND\s+(\d+)\s+COMPLETE.*?Success:\s*(\d+)/(\d+)", line)
        if complete_match:
            self.current_round = int(complete_match.group(1))
            success = int(complete_match.group(2))
            total = int(complete_match.group(3))
            self.success_count += success
            self.fail_count += (total - success)
            self.processed_links += total
            self.status = f"Round {self.current_round} complete"

        # Per-link: track expired only. Success/fail/processed are updated from
        # the "ROUND COMPLETE" summary below - counting both would double them.
        if "Status: EXPIRED / DELETED" in line:
            self.expired_count += 1

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

    # ---------- Background Workers ----------
    def run_resolver_subprocess(self, rounds: int, concurrency: int = 2):
        """Worker: run CLI as subprocess and capture output in real-time"""
        self.total_rounds = rounds
        self.current_round = 0
        self.success_count = 0
        self.fail_count = 0
        self.expired_count = 0
        self.processed_links = 0
        self.status = "Starting..."
        self.add_log(f"Starting resolver: {rounds} rounds, concurrency={concurrency}", "success")

        try:
            # Build command (-u = unbuffered so logs stream in real-time)
            cmd = [sys.executable, "-u", "main.py", "-c", str(concurrency)]
            if rounds > 1:
                cmd.extend(["--rounds", str(rounds)])

            self.worker_proc = subprocess.Popen(
                cmd,
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True
            )

            # Read output line by line in real-time
            if self.worker_proc and self.worker_proc.stdout:
                for line in iter(self.worker_proc.stdout.readline, ''):
                    if not self.running:
                        self.worker_proc.terminate()
                        break
                    self.parse_output_line(line)

            self.worker_proc.wait()
            
            if self.running:
                self.status = "Complete"
                self.add_log("Resolver finished", "success")

        except Exception as e:
            self.status = "Error"
            self.add_log(f"Error: {e}", "error")
        finally:
            self.worker_proc = None
            self.status = "Complete" if self.running else "Stopped"

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
                universal_newlines=True
            )

            # Read output line by line in real-time
            if self.worker_proc and self.worker_proc.stdout:
                for line in iter(self.worker_proc.stdout.readline, ''):
                    if not self.running:
                        self.worker_proc.terminate()
                        break
                    self.parse_output_line(line)

            self.worker_proc.wait()
            self.worker_proc = None
            
            if self.running:
                self.status = "Idle"
                self.add_log(f"Scraping done. Proxies: {self.proxy_count}", "success")

        except Exception as e:
            self.add_log(f"Scrape error: {e}", "error")
            self.status = "Idle"
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

    # ---------- Input Threads ----------
    def input_thread(self):
        """Single stdin reader: menu choice AND round prompts happen here, so
        two threads can never race for the same input line."""
        while self.running:
            try:
                choice = console.input("\n[bold bright_cyan]Select (1-4, Q):[/bold bright_cyan] ").strip().upper()
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

            self.menu_choice = choice

    # ---------- Main Loop ----------
    def handle_choice(self, choice: str):
        """Process menu selection"""
        if choice == "1":
            # Stop any running worker
            if self.worker_proc:
                self.worker_proc.terminate()
                self.worker_proc = None

            # Rounds/concurrency were already collected by input_thread
            # (no second stdin reader, no 30s UI freeze).
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
            if self.worker_proc:
                self.worker_proc.terminate()
                self.worker_proc = None
            threading.Thread(target=self.scrape_proxies_subprocess, daemon=True).start()

        elif choice == "3":
            self.show_other_functions()

        elif choice == "4":
            self.toggle_quiet()

        elif choice == "Q":
            if self.worker_proc:
                self.worker_proc.terminate()
            self.running = False

    def run(self):
        """Main TUI loop"""
        self.add_log("OUO Link Resolver TUI started", "success")
        self.add_log("Press 1-4 or Q to quit", "info")

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