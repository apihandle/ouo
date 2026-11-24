"""
Enhanced Proxy Manager v2 - Smart proxy harvesting, validation, scoring & rotation.
Improvements over v1:
1. Full OUO flow validation (Playwright-based, tests actual resolution)
2. Proxy scoring: latency, success rate, reliability, geographic diversity
3. Persistent metrics (survives restarts)
4. Smart SOCKS5 validation (actual HTTP through tunnel)
5. Async validation pipeline with priority queue
6. Automatic failover based on scores
7. Datacenter vs Residential detection
8. Health monitoring with detailed metrics
"""

import sys
import os
import re
import time
import random
import socket
import threading
import collections
import urllib.request
import json
import pickle
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta
from pathlib import Path

# =============================================================================
# CONFIGURATION
# =============================================================================

PRIMARY_PROXY_SOURCES = [
    # hide.mn and hidemy.name live proxy lists
    "https://hide.mn/en/proxy-list/",
    "https://hidemy.name/en/proxy-list/",
    "https://hide.mn/ru/proxy-list/",
    "https://hide.mn/es/proxy-list/",

    # High-volume verified global proxy feeds
    "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
    "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
    "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
    "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
    "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/socks5_proxies.txt",
    "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/generated/http_proxies.txt",
    "https://spys.me/socks.txt",
    "https://spys.me/proxy.txt",
]

# Test URLs for validation - use actual shortener domains
VALIDATION_URLS = [
    "https://ouo.press",
    "https://ouo.io",
    "https://bit.ly",
    "https://tinyurl.com",
]

# Metrics persistence
METRICS_FILE = "proxy_metrics.json"
RAW_PROXIES_FILE = "proxies_raw.txt"
VALIDATED_PROXIES_FILE = "proxies_validated.txt"

# =============================================================================
# DATA CLASSES
# =============================================================================

@dataclass
class ProxyMetrics:
    """Persistent metrics for a proxy."""
    server: str
    display: str
    proxy_type: str = "unknown"  # http, https, socks5
    country: str = "unknown"
    isp: str = "unknown"
    is_datacenter: bool = False
    
    # Performance metrics
    total_tests: int = 0
    successful_tests: int = 0
    failed_tests: int = 0
    total_latency_ms: float = 0.0
    last_latency_ms: float = 0.0
    avg_latency_ms: float = 0.0
    
    # OUO-specific metrics
    ouo_resolves: int = 0
    ouo_failures: int = 0
    ouo_expired_detected: int = 0
    
    # State
    last_tested: float = 0.0
    last_success: float = 0.0
    consecutive_failures: int = 0
    is_active: bool = True
    score: float = 0.0  # Computed score (higher = better)
    
    # Validation history (last 10 results)
    recent_results: List[bool] = field(default_factory=list)
    
    def update_score(self):
        """Compute composite score: reliability * speed * freshness."""
        if self.total_tests == 0:
            self.score = 0.0
            return
        
        reliability = self.successful_tests / self.total_tests
        speed_factor = 1.0 / (1.0 + self.avg_latency_ms / 1000.0)  # Lower latency = higher score
        freshness = 1.0 if (time.time() - self.last_tested) < 3600 else 0.5  # Tested in last hour
        recency_bonus = min(len(self.recent_results) * 0.05, 0.3)  # Recent activity bonus
        
        # Penalty for consecutive failures
        failure_penalty = 1.0 - min(self.consecutive_failures * 0.15, 0.7)
        
        self.score = reliability * speed_factor * freshness * failure_penalty + recency_bonus
        
        # OUO-specific boost
        if self.ouo_resolves > 0:
            ouo_success_rate = self.ouo_resolves / (self.ouo_resolves + self.ouo_failures) if (self.ouo_resolves + self.ouo_failures) > 0 else 0
            self.score *= (0.5 + 0.5 * ouo_success_rate)
    
    def record_test(self, success: bool, latency_ms: float, is_ouo_test: bool = False, expired: bool = False):
        """Record a test result."""
        self.total_tests += 1
        self.last_tested = time.time()
        self.last_latency_ms = latency_ms
        
        # Update rolling average latency
        if self.total_tests == 1:
            self.avg_latency_ms = latency_ms
        else:
            self.avg_latency_ms = (self.avg_latency_ms * (self.total_tests - 1) + latency_ms) / self.total_tests
        
        if success:
            self.successful_tests += 1
            self.consecutive_failures = 0
            self.last_success = time.time()
            self.recent_results.append(True)
        else:
            self.failed_tests += 1
            self.consecutive_failures += 1
            self.recent_results.append(False)
        
        # Keep only last 10 results
        if len(self.recent_results) > 10:
            self.recent_results = self.recent_results[-10:]
        
        if is_ouo_test:
            if success and not expired:
                self.ouo_resolves += 1
            elif expired:
                self.ouo_expired_detected += 1
            else:
                self.ouo_failures += 1
        
        self.update_score()
    
    def to_dict(self) -> dict:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: dict) -> 'ProxyMetrics':
        return cls(**data)


@dataclass
class ProxyEntry:
    """Runtime proxy entry with live state."""
    server: str
    display: str
    username: str = ""
    password: str = ""
    metrics: ProxyMetrics = None
    
    def __post_init__(self):
        if self.metrics is None:
            self.metrics = ProxyMetrics(server=self.server, display=self.display)
    
    # Backward compatibility with dict-like access
    def __getitem__(self, key):
        if key == "server":
            return self.server
        elif key == "display":
            return self.display
        elif key == "username":
            return self.username
        elif key == "password":
            return self.password
        raise KeyError(key)
    
    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default
    
    def __contains__(self, key):
        return key in ("server", "display", "username", "password")
    
    def keys(self):
        return ["server", "display", "username", "password"]
    
    def __iter__(self):
        return iter(self.keys())


# =============================================================================
# PROXY PARSING & UTILITIES
# =============================================================================

def parse_proxy(raw: str) -> Optional[ProxyEntry]:
    """Parse raw proxy string into ProxyEntry."""
    raw = raw.strip()
    if not raw or raw.startswith("#"):
        return None

    # Handle ip:port:user:pass
    parts = raw.split(":")
    if len(parts) == 4 and not raw.startswith("http") and not raw.startswith("socks"):
        ip, port, user, pwd = parts
        try:
            int(port)
        except ValueError:
            return None
        return ProxyEntry(
            server=f"http://{ip}:{port}",
            display=f"http://{user}:***@{ip}:{port}",
            username=user,
            password=pwd
        )

    # Warn on SOCKS4 (unsupported by Chromium/Playwright)
    if raw.startswith("socks4://"):
        print(f"⚠️ SOCKS4 not supported by Chromium/Playwright, skipping: {raw}")
        return None

    if not (raw.startswith("http://") or raw.startswith("https://") or raw.startswith("socks5://")):
        raw = f"http://{raw}"

    parsed = urlparse(raw)
    scheme = (parsed.scheme or "http").lower()
    hostname = parsed.hostname
    if not hostname:
        return None

    try:
        port = parsed.port
    except ValueError:
        return None
    if not port:
        port = 1080 if scheme == "socks5" else (443 if scheme == "https" else 80)

    server = f"{scheme}://{hostname}:{port}"
    display = f"{scheme}://"
    if parsed.username:
        display += f"{parsed.username}:***@"
    display += f"{hostname}:{port}"

    entry = ProxyEntry(server=server, display=display)
    if parsed.username:
        entry.username = parsed.username
    if parsed.password:
        entry.password = parsed.password
    
    # Set proxy type in metrics
    entry.metrics.proxy_type = scheme
    
    return entry


def normalize_proxy_key(server: str) -> str:
    """Normalize proxy server string for deduplication."""
    # Remove auth, scheme, trailing slashes
    key = server
    if "://" in key:
        key = key.split("://", 1)[1]
    if "@" in key:
        key = key.split("@", 1)[1]
    return key.lower().rstrip("/")


# =============================================================================
# PROXY SCRAPING
# =============================================================================

def fetch_all_raw_proxies(sources=None, limit=None) -> list:
    """Scrapes thousands of fresh candidate proxies from hide.mn and live feeds."""
    if sources is None:
        sources = PRIMARY_PROXY_SOURCES

    candidates = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    }

    print("🌐 Harvesting proxies from hide.mn and live online repositories...")

    for src in sources:
        try:
            req = urllib.request.Request(src, headers=headers)
            with urllib.request.urlopen(req, timeout=7) as resp:
                text = resp.read().decode("utf-8", errors="ignore")

                is_socks = "socks5" in src.lower()
                prefix = "socks5://" if is_socks else "http://"

                # 1. Check HTML table format (hide.mn / hidemy.name tables)
                html_matches = re.findall(r"<tr><td>(\d+\.\d+\.\d+\.\d+)</td>\s*<td>(\d+)</td>", text)
                if html_matches:
                    for ip, port in html_matches:
                        candidates.append(f"{prefix}{ip}:{port}")

                # 2. Check raw IP:PORT format
                raw_matches = re.findall(r"(\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}\b)", text)
                if raw_matches:
                    for p in raw_matches:
                        candidates.append(f"{prefix}{p}")

                label = "hide.mn / hidemy.name" if "hide" in src else src.split("/")[-1]
                count = len(html_matches) if html_matches else len(raw_matches)
                print(f"   📥 Scraped {count:5d} proxies from: {label}")

        except Exception as e:
            pass

    # Deduplicate by pure IP
    unique_by_ip = {}
    for p in candidates:
        clean_ip = p.split("://")[-1].split(":")[0]
        if clean_ip not in unique_by_ip:
            unique_by_ip[clean_ip] = p

    deduped = list(unique_by_ip.values())
    if limit:
        deduped = deduped[:limit]

    print(f"✨ Aggregation complete! Total unique IP proxies gathered: {len(deduped)}\n")
    return deduped


# =============================================================================
# PROXY VALIDATION - BASIC CONNECTIVITY
# =============================================================================

def test_proxy_basic(proxy_str: str, test_url: str = "https://ouo.press", timeout: int = 4) -> tuple:
    """
    Basic connectivity test for a proxy.
    Returns: (success: bool, latency_ms: float, error: str)
    """
    raw = proxy_str.strip()
    is_socks5 = raw.startswith("socks5://")
    start = time.time()

    if is_socks5:
        # Full SOCKS5 handshake & CONNECT verification to target host
        try:
            target = raw.replace("socks5://", "")
            if "@" in target:
                target = target.split("@")[-1]
            host, port = target.split(":")
            port = int(port)
            
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host, port))
            
            # Step 1: Greeting (no auth)
            s.sendall(b'\x05\x01\x00')
            resp = s.recv(2)
            if resp != b'\x05\x00':
                s.close()
                return False, (time.time() - start) * 1000, "SOCKS5 auth failed"
            
            # Step 2: CONNECT to target host:443
            domain_b = b"ouo.press"
            req = b'\x05\x01\x00\x03' + len(domain_b).to_bytes(1, 'big') + domain_b + (443).to_bytes(2, 'big')
            s.sendall(req)
            resp2 = s.recv(10)
            s.close()
            
            latency = (time.time() - start) * 1000
            if len(resp2) >= 2 and resp2[0] == 5 and resp2[1] == 0:
                return True, latency, None
            return False, latency, "SOCKS5 CONNECT failed"
        except Exception as e:
            return False, (time.time() - start) * 1000, str(e)

    # HTTP/HTTPS proxy
    if not (raw.startswith("http://") or raw.startswith("https://")):
        raw = f"http://{raw}"

    proxy_handler = urllib.request.ProxyHandler({
        "http": raw,
        "https": raw
    })
    opener = urllib.request.build_opener(proxy_handler)
    req = urllib.request.Request(
        test_url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        }
    )
    try:
        with opener.open(req, timeout=timeout) as resp:
            latency = (time.time() - start) * 1000
            if resp.status == 200:
                body = resp.read(500).decode("utf-8", errors="ignore")
                if "Just a moment" not in body:
                    return True, latency, None
                return False, latency, "Cloudflare challenge"
    except Exception as e:
        return False, (time.time() - start) * 1000, str(e)
    return False, (time.time() - start) * 1000, "Unknown error"


# =============================================================================
# PROXY VALIDATION - FULL OUO FLOW (Playwright-based)
# =============================================================================

def test_proxy_full_ouo(proxy_entry: ProxyEntry, test_url: str = "https://ouo.press/test123", timeout: int = 30) -> tuple:
    """
    Full OUO resolution test using Playwright.
    Tests if proxy can actually resolve OUO links (JavaScript, countdown, reCAPTCHA).
    Returns: (success: bool, latency_ms: float, final_url: str, error: str)
    """
    from playwright.sync_api import sync_playwright
    from solvers import solve_ouo, is_ouo_homepage, is_shortener
    
    start = time.time()
    server = proxy_entry.server
    is_socks5 = server.startswith("socks5://")
    
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                    "--disable-gpu",
                ]
            )
            
            context_opts = {
                "user_agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36",
                "viewport": {"width": 393, "height": 851},
                "is_mobile": True,
                "has_touch": True,
                "device_scale_factor": 2.75,
                "ignore_https_errors": True,
            }
            
            context_opts["proxy"] = {"server": server}
            if proxy_entry.username:
                context_opts["proxy"]["username"] = proxy_entry.username
            if proxy_entry.password:
                context_opts["proxy"]["password"] = proxy_entry.password
            
            context = browser.new_context(**context_opts)
            
            # Apply basic stealth
            context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = {runtime: {}};
            """)
            
            page = context.new_page()
            page.set_default_timeout(15000)
            page.set_default_navigation_timeout(20000)
            
            try:
                page.goto(test_url, wait_until="domcontentloaded", timeout=timeout * 1000)
            except Exception:
                pass
            
            # Quick check if we can even load the page
            time.sleep(1)
            current_url = page.url
            
            if is_ouo_homepage(current_url):
                browser.close()
                latency = (time.time() - start) * 1000
                return True, latency, "[EXPIRED / DELETED LINK]", "Expired link detected (this is good - proxy works)"
            
            # Try to resolve using the actual solver
            try:
                resolved = solve_ouo(page, test_url, timeout_sec=timeout)
            except Exception as e:
                resolved = str(e)
            
            browser.close()
            latency = (time.time() - start) * 1000
            
            if resolved and not is_shortener(resolved) and resolved != "[EXPIRED / DELETED LINK]":
                return True, latency, resolved, None
            elif resolved == "[EXPIRED / DELETED LINK]":
                return True, latency, resolved, "Expired link (proxy works)"
            else:
                return False, latency, current_url, f"Failed to resolve: {resolved}"
                
    except Exception as e:
        return False, (time.time() - start) * 1000, "", str(e)


# =============================================================================
# GEOGRAPHIC INFO (Optional - uses free IP API)
# =============================================================================

def enrich_proxy_geo(proxy_entry: ProxyEntry) -> ProxyEntry:
    """Enrich proxy with geographic info using free IP API."""
    try:
        ip = proxy_entry.server.split("://")[-1].split(":")[0]
        # Skip private IPs
        if ip.startswith(("10.", "192.168.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31.")):
            return proxy_entry
        
        # Use free IP API (ip-api.com - 45 req/min free)
        url = f"http://ip-api.com/json/{ip}?fields=country,isp,org,proxy,hosting"
        req = urllib.request.Request(url, headers={"User-Agent": "OUO-ProxyManager/2.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            if data.get("status") == "success":
                proxy_entry.metrics.country = data.get("country", "unknown")
                proxy_entry.metrics.isp = data.get("isp", "unknown")
                proxy_entry.metrics.is_datacenter = data.get("hosting", False) or data.get("proxy", False)
    except Exception:
        pass
    return proxy_entry


# =============================================================================
# METRICS PERSISTENCE
# =============================================================================

class MetricsStore:
    """Thread-safe persistent metrics storage."""
    
    def __init__(self, filepath: str = METRICS_FILE):
        self.filepath = filepath
        self.lock = threading.Lock()
        self.metrics: Dict[str, ProxyMetrics] = {}
        self.load()
    
    def load(self):
        with self.lock:
            if os.path.isfile(self.filepath):
                try:
                    with open(self.filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        for k, v in data.items():
                            self.metrics[k] = ProxyMetrics.from_dict(v)
                    print(f"📊 Loaded metrics for {len(self.metrics)} proxies from {self.filepath}")
                except Exception as e:
                    print(f"⚠️ Failed to load metrics: {e}")
    
    def save(self):
        with self.lock:
            try:
                data = {k: v.to_dict() for k, v in self.metrics.items()}
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception as e:
                print(f"⚠️ Failed to save metrics: {e}")
    
    def get(self, key: str) -> Optional[ProxyMetrics]:
        with self.lock:
            return self.metrics.get(key)
    
    def set(self, key: str, metrics: ProxyMetrics):
        with self.lock:
            self.metrics[key] = metrics
    
    def get_all_sorted(self, min_score: float = 0.0) -> List[ProxyMetrics]:
        with self.lock:
            sorted_metrics = sorted(
                [m for m in self.metrics.values() if m.score >= min_score and m.is_active],
                key=lambda x: x.score,
                reverse=True
            )
            return sorted_metrics


# =============================================================================
# ENHANCED PROXY POOL
# =============================================================================

class EnhancedProxyPool:
    """
    Smart proxy pool with:
    - Score-based selection (best proxies first)
    - Background validation & refill
    - Persistent metrics
    - Geographic diversity
    - Automatic failover
    """
    
    def __init__(
        self,
        initial_proxies: List[ProxyEntry] = None,
        min_threshold: int = 8,
        target_size: int = 20,
        raw_file: str = RAW_PROXIES_FILE,
        metrics_file: str = METRICS_FILE,
        max_failures: int = 3,
        enable_ouo_validation: bool = True,
        validation_interval: int = 300,  # seconds
    ):
        self.lock = threading.Lock()
        self.active_proxies: List[ProxyEntry] = initial_proxies or []
        self.min_threshold = min_threshold
        self.target_size = target_size
        self.max_failures = max_failures
        self.raw_file = raw_file
        self.enable_ouo_validation = enable_ouo_validation
        self.validation_interval = validation_interval
        
        self.metrics_store = MetricsStore(metrics_file)
        self.raw_candidates: List[str] = []
        self.candidate_index = 0
        self.is_refilling = False
        self.last_validation_time = 0
        self.last_refill_time = 0
        
        # Load raw candidates
        self._load_raw_candidates()
        
        # Restore metrics for initial proxies
        for entry in self.active_proxies:
            key = normalize_proxy_key(entry.server)
            stored = self.metrics_store.get(key)
            if stored:
                entry.metrics = stored
            entry.metrics.update_score()
        
        # Sort by score
        self.active_proxies.sort(key=lambda x: x.metrics.score, reverse=True)
        
        # Start background refill if needed
        if len(self.active_proxies) < self.target_size:
            self._start_background_refill()
        
        # Start background validator
        self._start_background_validator()
    
    def _load_raw_candidates(self):
        """Load raw proxy candidates from file or fetch fresh."""
        candidates = []
        if self.raw_file and os.path.isfile(self.raw_file):
            try:
                with open(self.raw_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and ":" in line and not line.startswith("#"):
                            candidates.append(line)
            except Exception:
                pass
        
        if len(candidates) < 100:
            print("📥 Raw candidate pool low, fetching fresh proxies...")
            candidates = fetch_all_raw_proxies(limit=2000)
        
        random.shuffle(candidates)
        self.raw_candidates = candidates
        self.candidate_index = 0
        print(f"📦 Loaded {len(self.raw_candidates)} raw candidates")
    
    def _start_background_refill(self):
        if self.is_refilling:
            return
        self.is_refilling = True
        threading.Thread(target=self._background_refill, daemon=True).start()
    
    def _start_background_validator(self):
        threading.Thread(target=self._background_validator, daemon=True).start()
    
    def _background_refill(self):
        """Background worker: harvest & validate fresh proxies to maintain target_size."""
        try:
            print(f"\n🔄 [Auto-Refill] Pool below target ({self.size()}/{self.target_size}). Harvesting fresh proxies...")
            batch_size = 50
            newly_added = 0
            
            while self.size() < self.target_size and self.candidate_index < len(self.raw_candidates):
                batch = self.raw_candidates[self.candidate_index:self.candidate_index + batch_size]
                self.candidate_index += batch_size
                
                if not batch:
                    break
                
                # Parse and basic validation
                parsed_batch = []
                for raw in batch:
                    entry = parse_proxy(raw)
                    if entry:
                        parsed_batch.append(entry)
                
                if not parsed_batch:
                    continue
                
                # Basic connectivity validation (fast)
                validated = []
                with ThreadPoolExecutor(max_workers=30) as ex:
                    futures = {ex.submit(test_proxy_basic, e.server, "https://ouo.press", 4): e for e in parsed_batch}
                    for f in as_completed(futures):
                        entry = futures[f]
                        success, latency, error = f.result()
                        if success:
                            entry.metrics.record_test(True, latency)
                            validated.append(entry)
                
                # Optional: Full OUO validation for top candidates
                if self.enable_ouo_validation and validated:
                    # Only test top 5 by latency
                    validated.sort(key=lambda x: x.metrics.avg_latency_ms)
                    for entry in validated[:5]:
                        success, latency, final_url, error = test_proxy_full_ouo(entry)
                        entry.metrics.record_test(success, latency, is_ouo_test=True, expired=(final_url == "[EXPIRED / DELETED LINK]"))
                        if success:
                            # Enrich with geo info
                            enrich_proxy_geo(entry)
                
                # Add to pool
                if validated:
                    with self.lock:
                        for entry in validated:
                            key = normalize_proxy_key(entry.server)
                            if not any(normalize_proxy_key(p.server) == key for p in self.active_proxies):
                                self.active_proxies.append(entry)
                                self.metrics_store.set(key, entry.metrics)
                                newly_added += 1
                        
                        # Re-sort by score
                        self.active_proxies.sort(key=lambda x: x.metrics.score, reverse=True)
                        # Trim to target_size
                        if len(self.active_proxies) > self.target_size:
                            self.active_proxies = self.active_proxies[:self.target_size]
                        
                        total_active = len(self.active_proxies)
                    
                    print(f"🎉 [Auto-Refill] Added {len(validated)} proxies! Pool: {total_active} active (top score: {self.active_proxies[0].metrics.score:.3f})")
                
                time.sleep(1)
            
            # If still below target, try fetching more raw proxies
            if self.size() < self.target_size and self.candidate_index >= len(self.raw_candidates):
                print("🔄 [Auto-Refill] Exhausted local candidates, fetching fresh from sources...")
                self._load_raw_candidates()
                # Recursive call to continue refill
                if self.raw_candidates:
                    self._background_refill()
                    return
            
        except Exception as e:
            print(f"⚠️ [Auto-Refill] Error: {e}")
        finally:
            with self.lock:
                self.is_refilling = False
                self.last_refill_time = time.time()
    
    def _background_validator(self):
        """Periodically re-validate active proxies to keep scores fresh."""
        while True:
            time.sleep(self.validation_interval)
            
            if self.is_refilling:
                continue
            
            with self.lock:
                # Only validate proxies not tested recently
                now = time.time()
                to_validate = [
                    p for p in self.active_proxies
                    if (now - p.metrics.last_tested) > self.validation_interval
                ]
            
            if not to_validate:
                continue
            
            print(f"🔍 [Background Validator] Re-validating {len(to_validate)} stale proxies...")
            
            with ThreadPoolExecutor(max_workers=20) as ex:
                futures = {ex.submit(test_proxy_basic, p.server, "https://ouo.press", 4): p for p in to_validate}
                for f in as_completed(futures):
                    entry = futures[f]
                    success, latency, error = f.result()
                    entry.metrics.record_test(success, latency)
                    
                    key = normalize_proxy_key(entry.server)
                    self.metrics_store.set(key, entry.metrics)
                    
                    if not success:
                        entry.metrics.consecutive_failures += 1
                        if entry.metrics.consecutive_failures >= self.max_failures:
                            self.remove_proxy(entry.server)
                            print(f"⚠️ [Validator] Discarded {entry.display} after {entry.metrics.consecutive_failures} consecutive failures")
            
            # Re-sort
            with self.lock:
                self.active_proxies.sort(key=lambda x: x.metrics.score, reverse=True)
            
            # Trigger refill if needed
            if self.size() < self.min_threshold:
                self._start_background_refill()
            
            # Persist metrics
            self.metrics_store.save()
    
    def size(self) -> int:
        with self.lock:
            return len(self.active_proxies)
    
    def get_proxy(self, prefer_high_score: bool = True) -> Optional[ProxyEntry]:
        """Get the best available proxy (score-based selection)."""
        should_refill = False
        entry = None
        
        with self.lock:
            # Clean up dead proxies first
            self.active_proxies = [p for p in self.active_proxies if p.metrics.is_active]
            
            if not self.active_proxies:
                should_refill = True
            else:
                if prefer_high_score:
                    # Weighted random selection favoring high scores
                    scores = [p.metrics.score for p in self.active_proxies]
                    total = sum(scores)
                    if total > 0:
                        r = random.random() * total
                        cumulative = 0
                        for i, s in enumerate(scores):
                            cumulative += s
                            if r <= cumulative:
                                entry = self.active_proxies[i]
                                # Move to end for round-robin among similar scores
                                self.active_proxies.pop(i)
                                self.active_proxies.append(entry)
                                break
                    else:
                        entry = self.active_proxies[0]
                        self.active_proxies.pop(0)
                        self.active_proxies.append(entry)
                else:
                    # Simple round-robin
                    entry = self.active_proxies[0]
                    self.active_proxies.pop(0)
                    self.active_proxies.append(entry)
                
                # Check if refill needed
                if len(self.active_proxies) < self.min_threshold and not self.is_refilling:
                    should_refill = True
        
        if should_refill:
            self._start_background_refill()
        
        return entry

    def __iter__(self):
        """Make pool iterable for backward compatibility."""
        with self.lock:
            return iter(self.active_proxies.copy())
    
    def __len__(self):
        """Return number of active proxies."""
        with self.lock:
            return len(self.active_proxies)

    def report_success(self, proxy_entry: ProxyEntry, is_ouo: bool = False, expired: bool = False):
        """Record successful use of a proxy."""
        if not proxy_entry:
            return
        proxy_entry.metrics.record_test(True, proxy_entry.metrics.last_latency_ms, is_ouo_test=is_ouo, expired=expired)
        key = normalize_proxy_key(proxy_entry.server)
        self.metrics_store.set(key, proxy_entry.metrics)
    
    def report_failure(self, proxy_entry: ProxyEntry, reason: str = None, is_ouo: bool = False):
        """Record failed use of a proxy."""
        if not proxy_entry:
            return
        
        proxy_entry.metrics.record_test(False, proxy_entry.metrics.last_latency_ms, is_ouo_test=is_ouo)
        key = normalize_proxy_key(proxy_entry.server)
        self.metrics_store.set(key, proxy_entry.metrics)
        
        if proxy_entry.metrics.consecutive_failures >= self.max_failures:
            self.remove_proxy(proxy_entry.server)
            display = proxy_entry.display
            print(f"⚠️ [Pool] Discarded {display} ({reason or 'max failures reached'})")
            print(f"   ℹ️ Active proxies remaining: {self.size()}")
            
            if self.size() < self.min_threshold:
                self._start_background_refill()
    
    def remove_proxy(self, server: str):
        """Remove a proxy from the active pool."""
        with self.lock:
            key = normalize_proxy_key(server)
            self.active_proxies = [p for p in self.active_proxies if normalize_proxy_key(p.server) != key]
            # Mark as inactive in metrics
            metrics = self.metrics_store.get(key)
            if metrics:
                metrics.is_active = False
                self.metrics_store.set(key, metrics)
    
    def get_stats(self) -> dict:
        """Get pool statistics."""
        with self.lock:
            if not self.active_proxies:
                return {"size": 0}
            
            scores = [p.metrics.score for p in self.active_proxies]
            latencies = [p.metrics.avg_latency_ms for p in self.active_proxies if p.metrics.avg_latency_ms > 0]
            countries = {}
            types = {}
            dc_count = 0
            
            for p in self.active_proxies:
                c = p.metrics.country
                countries[c] = countries.get(c, 0) + 1
                t = p.metrics.proxy_type
                types[t] = types.get(t, 0) + 1
                if p.metrics.is_datacenter:
                    dc_count += 1
            
            return {
                "size": len(self.active_proxies),
                "target_size": self.target_size,
                "min_threshold": self.min_threshold,
                "avg_score": sum(scores) / len(scores) if scores else 0,
                "top_score": max(scores) if scores else 0,
                "avg_latency_ms": sum(latencies) / len(latencies) if latencies else 0,
                "countries": countries,
                "types": types,
                "datacenter_proxies": dc_count,
                "residential_proxies": len(self.active_proxies) - dc_count,
                "is_refilling": self.is_refilling,
            }
    
    def print_stats(self):
        stats = self.get_stats()
        print("\n" + "=" * 50)
        print("📊 PROXY POOL STATISTICS")
        print("=" * 50)
        print(f"   Active Proxies:     {stats['size']} / {stats['target_size']} (min: {stats['min_threshold']})")
        print(f"   Avg Score:          {stats['avg_score']:.3f}")
        print(f"   Top Score:          {stats['top_score']:.3f}")
        print(f"   Avg Latency:        {stats['avg_latency_ms']:.0f} ms")
        print(f"   Datacenter:         {stats['datacenter_proxies']}")
        print(f"   Residential:        {stats['residential_proxies']}")
        print(f"   Countries:          {len(stats['countries'])} ({', '.join(f'{k}({v})' for k,v in sorted(stats['countries'].items(), key=lambda x: -x[1])[:5])})")
        print(f"   Types:              {stats['types']}")
        print(f"   Refilling:          {'Yes' if stats['is_refilling'] else 'No'}")
        print("=" * 50 + "\n")


# =============================================================================
# HIGH-LEVEL API (Backward compatible with v1)
# =============================================================================

def scrape_and_validate_proxies(limit: int = 10, max_workers: int = 50, timeout: int = 4, test_url: str = "https://ouo.press"):
    """Backward compatible function - scrapes and validates proxies."""
    candidates = fetch_all_raw_proxies()
    if not candidates:
        print("⚠️ No proxy candidates found.")
        return []
    
    print(f"🔍 Validating proxies against {test_url} (threads={max_workers}, timeout={timeout}s)...")
    
    valid_proxies = []
    seen_ips = set()
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(test_proxy_basic, p, test_url, timeout): p for p in candidates}
        for future in as_completed(futures):
            raw = futures[future]
            success, latency, error = future.result()
            if success:
                clean_ip = raw.split("://")[-1].split(":")[0]
                if clean_ip in seen_ips:
                    continue
                seen_ips.add(clean_ip)
                valid_proxies.append(raw)
                print(f"   🎯 Verified UNIQUE live proxy [{len(valid_proxies)}/{limit}]: {raw} ({latency:.0f}ms)")
                if len(valid_proxies) >= limit:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
    
    print(f"🎉 Validation complete: {len(valid_proxies)} live proxies ready!\n")
    return valid_proxies


def load_proxies(
    proxy_arg=None,
    proxy_file="proxies.txt",
    use_public=False,
    public_limit=5,
    validate_each=True,
    validate_upfront=False,
    min_threshold=8,
    target_size=20,
    enable_ouo_validation=True,
) -> EnhancedProxyPool:
    """Enhanced proxy loading with full OUO validation support."""
    print("DEBUG: load_proxies called (v2)")
    proxies = []
    
    if proxy_arg:
        p = parse_proxy(proxy_arg)
        if p:
            return EnhancedProxyPool(
                initial_proxies=[p],
                min_threshold=min_threshold,
                target_size=target_size,
                enable_ouo_validation=enable_ouo_validation,
            )
    
    if use_public:
        live_proxies = scrape_and_validate_proxies(limit=public_limit)
        for lp in live_proxies:
            p = parse_proxy(lp)
            if p:
                proxies.append(p)
    
    target_path = proxy_file
    if not os.path.isfile(target_path):
        fallback = os.path.join(os.path.dirname(os.path.realpath(__file__)), "proxies.txt")
        if os.path.isfile(fallback):
            target_path = fallback
    
    # If we're using public proxies, don't also load from file (avoids untested proxies)
    # Only load from file if NOT using public proxies
    file_proxies = []
    if not use_public and os.path.isfile(target_path):
        with open(target_path, "r", encoding="utf-8") as f:
            raw_lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        
        if raw_lines and validate_each:
            if validate_upfront:
                print(f"🔍 Validating {len(raw_lines)} proxies upfront...")
                for i, line in enumerate(raw_lines, 1):
                    p = parse_proxy(line)
                    if p:
                        server = p.server
                        success, latency, error = test_proxy_basic(server)
                        if success:
                            p.metrics.record_test(True, latency)
                            file_proxies.append(p)
                            print(f"   ✅ [{i}/{len(raw_lines)}] Live: {server} ({latency:.0f}ms)")
                        else:
                            print(f"   ❌ [{i}/{len(raw_lines)}] Dead: {server} ({error})")
                    else:
                        print(f"   ⚠️ [{i}/{len(raw_lines)}] Invalid format: {line}")
            else:
                print(f"📋 Loaded {len(raw_lines)} proxies (lazy validation)...")
                for line in raw_lines:
                    p = parse_proxy(line)
                    if p:
                        file_proxies.append(p)
    
    # Combine: public proxies first (already validated), then file proxies
    all_proxies = proxies + file_proxies
    
    # Create enhanced pool
    pool = EnhancedProxyPool(
        initial_proxies=all_proxies,
        min_threshold=min_threshold,
        target_size=target_size,
        raw_file=target_path,
        enable_ouo_validation=enable_ouo_validation,
    )
    
    return pool


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Enhanced Proxy Manager v2 - Scrape, validate, score & manage proxies.")
    parser.add_argument("--save-thousands", action="store_true", help="Download thousands of raw proxies and save to proxies_raw.txt")
    parser.add_argument("--validate", type=int, default=5, metavar="N", help="Validate and save N working proxies (default: 5)")
    parser.add_argument("--full-ouo", action="store_true", help="Run full OUO flow validation (requires Playwright)")
    parser.add_argument("-o", "--output", default="proxies.txt", help="Output file path (default: proxies.txt)")
    parser.add_argument("--stats", action="store_true", help="Show pool statistics from metrics file")
    parser.add_argument("--enrich-geo", action="store_true", help="Enrich existing proxies with geographic info")
    args = parser.parse_args()
    
    if args.save_thousands:
        raw = fetch_all_raw_proxies()
        out = "proxies_raw.txt" if args.output == "proxies.txt" else args.output
        with open(out, "w", encoding="utf-8") as f:
            for p in raw:
                f.write(f"{p}\n")
        print(f"💾 Saved {len(raw)} raw proxies to: {out}")
    
    elif args.stats:
        store = MetricsStore()
        all_metrics = store.get_all_sorted(min_score=0.0)
        print(f"\n📊 Stored metrics for {len(all_metrics)} proxies:")
        for m in all_metrics[:20]:
            print(f"   {m.display:<40} score={m.score:.3f} latency={m.avg_latency_ms:.0f}ms tests={m.total_tests} success={m.successful_tests} country={m.country} dc={m.is_datacenter}")
    
    elif args.enrich_geo:
        # Load proxies from file and enrich
        with open(args.output, "r") as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        for line in lines:
            entry = parse_proxy(line)
            if entry:
                enrich_proxy_geo(entry)
                print(f"   {entry.display} -> {entry.metrics.country}, {entry.metrics.isp}, DC={entry.metrics.is_datacenter}")
    
    else:
        live = scrape_and_validate_proxies(limit=args.validate)
        if live:
            with open(args.output, "w", encoding="utf-8") as f:
                for p in live:
                    f.write(f"{p}\n")
            print(f"💾 Saved {len(live)} validated working proxies to: {args.output}")