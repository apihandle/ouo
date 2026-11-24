#!/usr/bin/env python3
"""
OUO Link Resolver - One-command setup
Run: python3 setup.py
"""
import subprocess
import sys
import os

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))

# Use the interpreter that is actually running this script (works on Termux,
# venvs, pyenv, ...). The old hard-coded "/usr/bin/python3" does not exist on
# many systems.
PYTHON = sys.executable


def run(cmd, desc):
    print(f"🔧 {desc}...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"❌ Failed: {cmd}")
        print(result.stderr)
        return False
    if result.stdout.strip():
        print(result.stdout.strip())
    return True


def install_deps():
    # `rich` is required by the default TUI, previously it was never installed.
    pkgs = "playwright rich"
    if run(f'"{PYTHON}" -m pip install --user {pkgs}', "Installing Playwright + Rich"):
        return True
    print("⚠️  `--user` install failed, retrying into the current environment...")
    return run(f'"{PYTHON}" -m pip install {pkgs}', "Installing Playwright + Rich")


def main():
    print("=" * 50)
    print("🚀 OUO Link Resolver - Full Setup")
    print("=" * 50)
    print(f"🐍 Using interpreter: {PYTHON}")

    # 1. Install Python packages
    if not install_deps():
        return 1

    # 2. Install Chromium
    if not run(f'"{PYTHON}" -m playwright install chromium', "Installing Chromium browser"):
        return 1

    # 3. Install FFmpeg (for video/audio)
    run(f'"{PYTHON}" -m playwright install ffmpeg', "Installing FFmpeg")

    # 4. Make run.sh executable
    run_sh = os.path.join(SCRIPT_DIR, "run.sh")
    if os.path.exists(run_sh):
        os.chmod(run_sh, 0o755)
        print("✅ Made run.sh executable")

    print("\n" + "=" * 50)
    print("✅ Setup complete!")
    print("=" * 50)
    print("\n📖 Usage:")
    print("  python3 main.py               # Launch TUI (interactive)")
    print("  python3 main.py --once        # Single run on links.txt")
    print("  python3 main.py -c 3 --loop   # Infinite loop with 3 concurrent")
    print("  python3 main.py -P            # Auto-scrape proxies + resolve")
    print("  python3 main.py --fetch-proxies  # Just harvest proxies")
    print("\n📝 Edit links.txt with your OUO URLs (one per line)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
