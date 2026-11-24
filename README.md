# link — Universal Stealth Link Resolver

## Install (one-time)
```bash
python3 setup.py
```
Installs Playwright, Rich (TUI), Chromium, and dependencies.

## Usage
```bash
# Launch TUI (interactive mode)
python3 main.py
```
Opens terminal UI with live stats, logs, and menu.

```bash
# Resolve links from links.txt (default input)
python3 main.py --once
```
Runs one round on all links in `links.txt`, saves destinations to `impressions.txt`.

```bash
# Resolve a single URL
python3 main.py --once https://ouo.io/xyz
```
Resolves one link directly, prints final destination.

```bash
# With custom concurrency (max 4)
python3 main.py --once -c 3 links.txt
```
Processes 3 links in parallel (faster), uses `links.txt` as input.

```bash
# Auto-scrape & validate public proxies, then resolve
python3 main.py -P --once links.txt
```
Fetches fresh public proxies, validates them, then resolves links through working ones.

```bash
# Use your own proxy file (one per line: ip:port or user:pass@ip:port)
python3 main.py --once --proxy-file my_proxies.txt links.txt
```
Uses your proxy list instead of scraping public ones.

```bash
# Just harvest proxies to proxies_raw.txt
python3 main.py --fetch-proxies
```
Scrapes 12,000+ proxies from hide.mn/global feeds, saves to `proxies_raw.txt` (no resolution).

## Options
| Flag | Description |
|------|-------------|
| `--once` | Single run and exit |
| `--loop` | Infinite loop |
| `--rounds N` | Run N rounds then exit |
| `-c, --concurrency N` | Parallel workers (1-4, default: 3) |
| `-f, --file FILE` | Input file (default: links.txt) |
| `-o, --output FILE` | Output file (default: impressions.txt) |
| `-P, --public-proxies` | Auto-scrape & validate public proxies |
| `--proxy-file FILE` | Use your own proxy list |
| `--device auto|android|ios|mobile|desktop` | Device profile (default: auto) |
| `--timeout SEC` | Page load timeout (default: 12) |
| `--clean` | Auto-remove dead links from input file |

## Notes
- **Public proxies only work for simple redirects** (TinyURL, Bitly, etc.)
- **OUO, GPLinks, Droplink, Linkvertise need residential/mobile IPs** — public proxies will fail
- Without `--once`, `--loop` or `--rounds`, one single round runs by default
- Output saved to `impressions.txt` (final destinations)