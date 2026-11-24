#!/data/data/com.termux/files/usr/bin/env python3
"""
link - Fast, fully background Playwright tool to resolve, bypass, and extract links.
Features:
  1. ⚡ Stealth Evasion (Cloudflare Anti-Bot & Fingerprint spoofing)
  2. 🌐 Universal Multi-Shortener Support (OuO, GPLinks, Droplink, TinyURL, Bitly, etc.)
  3. 🚀 Multi-Threading (Parallel link processing with -c / --concurrency)
  4. ⚠️ Expired & Dead Link Auto-Detection
  5. 📊 End-of-Job Clean Summary Report
  6. 🛡️ Public Proxy Scraping, Rotation & Auto-Failover
"""

print("DEBUG: Script started")
import sys
import os

# Auto-add user site-packages for playwright (works without run.sh wrapper)
USER_SITE = os.path.expanduser("~/.local/lib/python3.14/site-packages")
if os.path.isdir(USER_SITE) and USER_SITE not in sys.path:
    sys.path.insert(0, USER_SITE)

import time
import random
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from stealth import apply_stealth, STEALTH_CHROMIUM_ARGS, DEFAULT_USER_AGENT, DEFAULT_VIEWPORT, generate_device_profile
from solvers import universal_resolve, is_shortener

# Try enhanced proxy manager v2 first, fall back to v1
load_proxies_v2 = None
EnhancedProxyPool = None
ProxyEntry = None
USE_ENHANCED_PROXY = False

try:
    from proxy_manager_v2 import (
        scrape_and_validate_proxies, fetch_all_raw_proxies, test_proxy_basic as test_proxy,
        EnhancedProxyPool as _EnhancedProxyPool, load_proxies_v2 as _load_proxies_v2, ProxyEntry as _ProxyEntry
    )
    EnhancedProxyPool = _EnhancedProxyPool
    load_proxies_v2 = _load_proxies_v2
    ProxyEntry = _ProxyEntry
    USE_ENHANCED_PROXY = True
except ImportError:
    try:
        from proxy_manager import scrape_and_validate_proxies, fetch_all_raw_proxies, test_proxy, DynamicProxyPool
    except ImportError:
        scrape_and_validate_proxies = None
        fetch_all_raw_proxies = None
        test_proxy = None
        DynamicProxyPool = None
    USE_ENHANCED_PROXY = False
    EnhancedProxyPool = None
    ProxyEntry = None
    DynamicProxyPool = None

print_lock = threading.Lock()

def safe_print(*args, **kwargs):
    with print_lock:
        print(*args, **kwargs)

def normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith("http://") and not url.startswith("https://"):
        url = f"https://{url}"
    if "ouo.io/" in url:
        url = url.replace("ouo.io/", "ouo.press/")
    return url

def parse_proxy(raw: str):
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
        return {
            "server": f"http://{ip}:{port}",
            "username": user,
            "password": pwd,
            "display": f"http://{user}:***@{ip}:{port}"
        }

    # Warn on SOCKS4 (unsupported by Chromium/Playwright)
    if raw.startswith("socks4://"):
        safe_print(f"⚠️ SOCKS4 is not supported by Chromium/Playwright, skipping: {raw}")
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

    d = {
        "server": f"{scheme}://{hostname}:{port}",
    }
    if parsed.username:
        d["username"] = parsed.username
    if parsed.password:
        d["password"] = parsed.password

    display = f"{scheme}://"
    if parsed.username:
        display += f"{parsed.username}:***@"
    display += f"{hostname}:{port}"
    d["display"] = display

    return d

def load_proxies(proxy_arg=None, proxy_file="proxies.txt", use_public=False, public_limit=5, validate_each=True, validate_upfront=True, min_threshold=8, target_size=20, enable_ouo_validation=True):
    print("DEBUG: load_proxies called")
    
    # Use enhanced proxy manager if available
    if USE_ENHANCED_PROXY:
        return load_proxies_v2(
            proxy_arg=proxy_arg,
            proxy_file=proxy_file,
            use_public=use_public,
            public_limit=public_limit,
            validate_each=validate_each,
            validate_upfront=validate_upfront,
            min_threshold=min_threshold,
            target_size=target_size,
            enable_ouo_validation=enable_ouo_validation,
        )
    
    # Fallback to v1 logic
    def load_proxies_v1(proxy_arg=None, proxy_file="proxies.txt", use_public=False, public_limit=5, validate_each=True, validate_upfront=True, min_threshold=8, target_size=20, enable_ouo_validation=True):
        print("DEBUG: load_proxies called (v1 fallback)")
        proxies = []

        if proxy_arg:
            p = parse_proxy(proxy_arg)
            if p:
                return [p]

        if use_public:
            if scrape_and_validate_proxies:
                live_proxies = scrape_and_validate_proxies(limit=public_limit)
                for lp in live_proxies:
                    p = parse_proxy(lp)
                    if p:
                        proxies.append(p)
            else:
                safe_print("⚠️ proxy_manager module not found, skipping public proxy scraping.")

        target_path = proxy_file
        if not os.path.isfile(target_path):
            fallback = os.path.join(SCRIPT_DIR, "proxies.txt")
            if os.path.isfile(fallback):
                target_path = fallback

        if os.path.isfile(target_path):
            with open(target_path, "r", encoding="utf-8") as f:
                raw_lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]

            if raw_lines and validate_each:
                if validate_upfront:
                    safe_print(f"🔍 Validating {len(raw_lines)} proxies upfront...")
                    if test_proxy:
                        for i, line in enumerate(raw_lines, 1):
                            p = parse_proxy(line)
                            if p:
                                server = p.get("server", "")
                                if test_proxy(server):
                                    proxies.append(p)
                                    safe_print(f"   ✅ [{i}/{len(raw_lines)}] Live: {server}")
                                else:
                                    safe_print(f"   ❌ [{i}/{len(raw_lines)}] Dead: {server}")
                            else:
                                safe_print(f"   ⚠️ [{i}/{len(raw_lines)}] Invalid format: {line}")
                else:
                    safe_print(f"📋 Loaded {len(raw_lines)} proxies (lazy validation: will test one-by-one at use time)...")
                    for line in raw_lines:
                        p = parse_proxy(line)
                        if p:
                            proxies.append(p)

        # Wrap in DynamicProxyPool for centralized validation & rotation
        if proxies and DynamicProxyPool:
            safe_print(f"🌐 Initializing DynamicProxyPool with {len(proxies)} proxies (target: {target_size}, min: {min_threshold})")
            return DynamicProxyPool(initial_proxies=proxies, min_threshold=min_threshold, target_size=target_size, raw_file=target_path)
        
        return proxies
    
    return load_proxies_v1(proxy_arg, proxy_file, use_public, public_limit, validate_each, validate_upfront, min_threshold, target_size)

def resolve_single_link(browser, item, proxy_list=None, timeout_sec=30,
                        device_type="auto", dwell_sec=0):
    idx, total, raw_url = item
    url = normalize_url(raw_url)
    profile = generate_device_profile(device_type)
    safe_print(f"[{idx}/{total}] 🔍 Resolving: {url}")
    safe_print(f"[{idx}/{total}] 📱 Device: {profile['label']}")

    def handle_popup(popup):
        try:
            popup.wait_for_load_state("domcontentloaded", timeout=3500)
            time.sleep(random.uniform(1.2, 2.5))
        except Exception:
            pass
        finally:
            try:
                popup.close()
            except Exception:
                pass

    def get_proxy_server(proxy):
        """Extract server string from proxy (dict or ProxyEntry)."""
        if proxy is None:
            return None
        if hasattr(proxy, 'server'):  # ProxyEntry
            return proxy.server
        return proxy.get("server", "")

    def get_proxy_display(proxy):
        """Extract display string from proxy (dict or ProxyEntry)."""
        if proxy is None:
            return "unknown"
        if hasattr(proxy, 'display'):  # ProxyEntry
            return proxy.display
        return proxy.get("display", proxy.get("server", "unknown"))

    def get_proxy_auth(proxy):
        """Extract username/password from proxy (dict or ProxyEntry)."""
        if proxy is None:
            return None, None
        if hasattr(proxy, 'username'):  # ProxyEntry
            return proxy.username, proxy.password
        return proxy.get("username"), proxy.get("password")

    def run_with_proxy(proxy_to_use):
        display = get_proxy_display(proxy_to_use)
        safe_print(f"[{idx}/{total}] 🛡️  Proxy: {display}")
        context = None
        try:
            context_opts = {
                "user_agent": profile["user_agent"],
                "viewport": profile["viewport"],
                "is_mobile": profile["is_mobile"],
                "has_touch": profile["has_touch"],
                "device_scale_factor": profile["device_scale_factor"],
                "ignore_https_errors": True
            }
            server = get_proxy_server(proxy_to_use)
            context_opts["proxy"] = {"server": server}
            username, password = get_proxy_auth(proxy_to_use)
            if username:
                context_opts["proxy"]["username"] = username
            if password:
                context_opts["proxy"]["password"] = password

            context = browser.new_context(**context_opts)
            apply_stealth(context, profile=profile)

            page = context.new_page()
            page.on("popup", handle_popup)

            res = universal_resolve(
                page, url, timeout_sec=timeout_sec, proxy=proxy_to_use,
                is_mobile=profile["is_mobile"], dwell_sec=dwell_sec
            )

            if res and not is_shortener(res):
                return res, proxy_to_use
            else:
                return None, None
        except Exception as err:
            safe_print(f"[{idx}/{total}] ⚠️ Proxy error: {err}")
            return None, None
        finally:
            if context:
                try:
                    context.close()
                except Exception:
                    pass

    def run_direct_fallback():
        safe_print(f"[{idx}/{total}] 🌐 Fallback: Trying direct connection...")
        d_ctx = None
        try:
            d_ctx = browser.new_context(
                user_agent=profile["user_agent"],
                viewport=profile["viewport"],
                is_mobile=profile["is_mobile"],
                has_touch=profile["has_touch"],
                device_scale_factor=profile["device_scale_factor"],
                ignore_https_errors=True
            )
            apply_stealth(d_ctx, profile=profile)
            d_page = d_ctx.new_page()
            d_page.on("popup", handle_popup)
            return universal_resolve(
                d_page, url, timeout_sec=timeout_sec,
                is_mobile=profile["is_mobile"], dwell_sec=dwell_sec
            )
        except Exception as e:
            safe_print(f"[{idx}/{total}] ❌ Direct fallback failed: {e}")
            return None
        finally:
            if d_ctx:
                try:
                    d_ctx.close()
                except Exception:
                    pass

    # Track if we used enhanced proxy pool
    used_enhanced_pool = USE_ENHANCED_PROXY and EnhancedProxyPool and isinstance(proxy_list, EnhancedProxyPool)
    used_dynamic_pool = (not USE_ENHANCED_PROXY) and DynamicProxyPool and isinstance(proxy_list, DynamicProxyPool)
    
    # Try proxies
    if proxy_list and test_proxy:
        if used_enhanced_pool:
            # Enhanced pool: get best proxy, try resolution, report result
            max_attempts = min(5, proxy_list.size())
            for attempt in range(max_attempts):
                proxy = proxy_list.get_proxy()
                if not proxy:
                    safe_print(f"[{idx}/{total}] ⚠️ No working proxies in pool")
                    break
                
                display = get_proxy_display(proxy)
                safe_print(f"[{idx}/{total}] 🛡️  Using validated proxy: {display} (score: {proxy.metrics.score:.3f})")
                final_destination, working_proxy = run_with_proxy(proxy)
                
                is_ouo = "ouo." in url
                expired = final_destination == "[EXPIRED / DELETED LINK]"
                
                if final_destination:
                    proxy_list.report_success(proxy, is_ouo=is_ouo, expired=expired)
                    if expired:
                        safe_print(f"[{idx}/{total}] ⚠️ Status: EXPIRED / DELETED (No longer active on OUO)\n")
                    else:
                        safe_print(f"[{idx}/{total}] 👉 Final Destination: {final_destination}\n")
                    return {
                        "index": idx,
                        "input_url": raw_url,
                        "destination": final_destination,
                        "status": "EXPIRED" if expired else "SUCCESS"
                    }, working_proxy
                
                safe_print(f"[{idx}/{total}] ⚠️ Proxy worked but resolution failed, trying next...")
                proxy_list.report_failure(proxy, "resolution failed", is_ouo=is_ouo)
            
            safe_print(f"[{idx}/{total}] ⚠️ Exhausted all proxies in pool")
        
        elif used_dynamic_pool:
            # Original DynamicProxyPool logic
            proxy = proxy_list.get_proxy()
            if proxy:
                server = proxy.get("server", "")
                safe_print(f"[{idx}/{total}] 🛡️  Using validated proxy: {proxy['display']}")
                final_destination, working_proxy = run_with_proxy(proxy)
                if final_destination:
                    return {
                        "index": idx,
                        "input_url": raw_url,
                        "destination": final_destination,
                        "status": "EXPIRED" if final_destination == "[EXPIRED / DELETED LINK]" else "SUCCESS"
                    }, working_proxy
                safe_print(f"[{idx}/{total}] ⚠️ Proxy worked but resolution failed, trying next...")
                proxy_list.report_failure(proxy, "resolution failed")
                # Try next proxy from pool
                proxy = proxy_list.get_proxy()
                while proxy:
                    safe_print(f"[{idx}/{total}] 🛡️  Using validated proxy: {proxy['display']}")
                    final_destination, working_proxy = run_with_proxy(proxy)
                    if final_destination:
                        return final_destination, working_proxy
                    safe_print(f"[{idx}/{total}] ⚠️ Proxy worked but resolution failed, trying next...")
                    proxy_list.report_failure(proxy, "resolution failed")
                    proxy = proxy_list.get_proxy()
            else:
                safe_print(f"[{idx}/{total}] ⚠️ No working proxies in pool")
        
        else:
            # Old logic for raw proxy list
            for proxy in proxy_list:
                server = get_proxy_server(proxy)
                display = get_proxy_display(proxy)
                safe_print(f"[{idx}/{total}] 🔎 Testing proxy: {server}")
                if test_proxy(server):
                    safe_print(f"[{idx}/{total}] ✅ Proxy validated, using it")
                    final_destination, working_proxy = run_with_proxy(proxy)
                    if final_destination:
                        return {
                            "index": idx,
                            "input_url": raw_url,
                            "destination": final_destination,
                            "status": "EXPIRED" if final_destination == "[EXPIRED / DELETED LINK]" else "SUCCESS"
                        }, working_proxy
                    safe_print(f"[{idx}/{total}] ⚠️ Proxy worked but resolution failed, trying next...")
                else:
                    safe_print(f"[{idx}/{total}] ❌ Proxy dead, trying next...")

    # No working proxy found, try direct
    final_destination = run_direct_fallback()

    status = "FAILED"
    if final_destination == "[EXPIRED / DELETED LINK]":
        status = "EXPIRED"
        safe_print(f"[{idx}/{total}] ⚠️ Status: EXPIRED / DELETED (No longer active on OUO)\n")
    elif final_destination and not is_shortener(final_destination):
        status = "SUCCESS"
        safe_print(f"[{idx}/{total}] 👉 Final Destination: {final_destination}\n")
    else:
        status = "FAILED"
        safe_print(f"[{idx}/{total}] ❌ Could not resolve destination.\n")

    return {
        "index": idx,
        "input_url": raw_url,
        "destination": final_destination,
        "status": status
    }, None

def worker_batch_task(batch_items, proxy_list, timeout_sec, device_type="auto", dwell_sec=0):
    """
    Worker task that launches Chromium once and processes all items in its batch.
    Tries proxies one by one with lazy validation for each link.
    """
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=STEALTH_CHROMIUM_ARGS
        )
        try:
            for item in batch_items:
                r, _ = resolve_single_link(
                    browser, item, proxy_list=proxy_list,
                    timeout_sec=timeout_sec,
                    device_type=device_type, dwell_sec=dwell_sec
                )
                results.append(r)
        finally:
            browser.close()
    return results

def process_links(urls, proxies=None, concurrency=2, timeout_sec=30, output_file=None,
                  clean_input_file=None, loop=False, rounds=1, delay_sec=5, min_proxies=5,
                  device_type="auto", dwell_sec=0):
    total = len(urls)
    concurrency = max(1, min(concurrency, 4))

    proxy_list = proxies  # Use raw proxy list directly for lazy validation

    # Get proxy count for display
    used_enhanced_pool = USE_ENHANCED_PROXY and EnhancedProxyPool and isinstance(proxy_list, EnhancedProxyPool)
    used_dynamic_pool = (not USE_ENHANCED_PROXY) and DynamicProxyPool and isinstance(proxy_list, DynamicProxyPool)
    
    if used_enhanced_pool:
        stats = proxy_list.get_stats()
        proxy_count = stats['size']
        proxy_mode = f"🌐 Proxy Mode:         EnhancedProxyPool v2 ({proxy_count} active, target: {stats['target_size']}, min: {stats['min_threshold']})"
        if stats['countries']:
            top_countries = ', '.join(f"{k}({v})" for k,v in sorted(stats['countries'].items(), key=lambda x: -x[1])[:3])
            proxy_mode += f" | Countries: {top_countries}"
        if stats['avg_latency_ms'] > 0:
            proxy_mode += f" | Avg Latency: {stats['avg_latency_ms']:.0f}ms"
    elif used_dynamic_pool:
        proxy_count = proxy_list.size()
        proxy_mode = f"🌐 Proxy Mode:         DynamicProxyPool v1 ({proxy_count} active, target: {proxy_list.target_size}, min: {proxy_list.min_threshold})"
    elif proxy_list:
        proxy_count = len(proxy_list)
        proxy_mode = f"🌐 Proxy Mode:         {proxy_count} proxies (Lazy Validation: test one-by-one at use time)"
    else:
        proxy_count = 0
        proxy_mode = "🌐 Proxy Mode:         Direct Connection (No proxy)"

    print(f"\n🚀 Autonomous Link Resolver Engine Started:")
    print(f"   📊 Total Links:        {total}")
    print(f"   ⚡ Concurrency:        {concurrency} parallel worker(s)")
    print(f"   🛡️  Stealth Mode:       ACTIVE (Anti-Detection & Automation Hidden)")
    print(f"   📱 Device Profile:     {device_type.upper()} Mode (Android / iOS / Desktop)")
    if dwell_sec > 0:
        print(f"   ⏳ Monetization Dwell: ACTIVE ({dwell_sec}s reading dwell on destination)")
    print(f"   {proxy_mode}")
    print("DEBUG: About to start processing...")

    total_sessions_processed = 0
    total_sessions_success = 0
    round_idx = 0
    global_start_time = time.time()

    try:
        while True:
            round_idx += 1

            if loop:
                print(f"\n{'='*60}\n🔄 ROUND {round_idx} (Infinite Loop Mode)\n{'='*60}")
            elif rounds > 1:
                print(f"\n{'='*60}\n🔄 ROUND {round_idx} of {rounds}\n{'='*60}")

            items = [(i, total, u) for i, u in enumerate(urls, 1)]
            results = []

            round_start = time.time()
            if concurrency == 1 or total == 1:
                results = worker_batch_task(items, proxy_list, timeout_sec, device_type=device_type, dwell_sec=dwell_sec)
            else:
                actual_workers = min(concurrency, total)
                batches = [[] for _ in range(actual_workers)]
                for i, item in enumerate(items):
                    batches[i % actual_workers].append(item)

                with ThreadPoolExecutor(max_workers=actual_workers) as executor:
                    futures = [executor.submit(worker_batch_task, b, proxy_list, timeout_sec, device_type, dwell_sec) for b in batches if b]
                    for f in as_completed(futures):
                        try:
                            batch_res = f.result()
                            results.extend(batch_res)
                        except Exception as e:
                            safe_print(f"⚠️ Worker batch error: {e}")

            results.sort(key=lambda x: x["index"])
            round_elapsed = round(time.time() - round_start, 1)

            success_count = sum(1 for r in results if r["status"] == "SUCCESS")
            expired_count = sum(1 for r in results if r["status"] == "EXPIRED")
            failed_count = sum(1 for r in results if r["status"] == "FAILED")

            total_sessions_processed += total
            total_sessions_success += success_count

            print("-" * 60)
            print(f"📊 ROUND {round_idx} COMPLETE ({round_elapsed}s) | Success: {success_count}/{total}")
            print("-" * 60)

            # Show proxy pool stats every round for enhanced pool
            if used_enhanced_pool and round_idx % 3 == 0:
                proxy_list.print_stats()

            # Save / append to output_file if requested
            if output_file:
                try:
                    with open(output_file, "a", encoding="utf-8") as out_f:
                        for r in results:
                            if r["status"] == "SUCCESS" and r["destination"]:
                                out_f.write(f"[Round {round_idx}] {r['input_url']} -> {r['destination']}\n")
                    print(f"💾 Logged destinations to: {output_file}")
                except Exception as e:
                    print(f"⚠️ Could not write to output file: {e}")

            # Prune confirmed dead links from input file if --clean requested on first round
            if round_idx == 1 and clean_input_file and os.path.isfile(clean_input_file):
                expired_urls = set(r["input_url"] for r in results if r["status"] == "EXPIRED")
                if expired_urls:
                    working_urls = [u for u in urls if u not in expired_urls]
                    if working_urls:
                        try:
                            with open(clean_input_file, "w", encoding="utf-8") as in_f:
                                for u in working_urls:
                                    in_f.write(f"{u}\n")
                            print(f"🧹 Pruned {len(expired_urls)} confirmed expired link(s) from '{clean_input_file}'. Kept {len(working_urls)} link(s).\n")
                            urls = working_urls
                            total = len(urls)
                        except Exception as e:
                            print(f"⚠️ Could not write to input file: {e}")
                    else:
                        print("⚠️ All links were expired. Skipping pruning to protect input file.\n")
                else:
                    print("🧹 Prune check: No expired/dead links found to remove.\n")

            if not loop and round_idx >= rounds:
                break

            print(f"⏳ Waiting {delay_sec}s before starting next round... (Press Ctrl+C to stop)")
            time.sleep(delay_sec)

    except KeyboardInterrupt:
        print("\n\n🛑 Automated Looper stopped by user.")

    total_elapsed = round(time.time() - global_start_time, 1)
    print("\n" + "=" * 60)
    print("📊 OVERALL AUTOMATION SESSION SUMMARY")
    print("=" * 60)
    print(f"  🔄 Rounds Executed:         {round_idx}")
    print(f"  🔢 Total Visits Attempted:  {total_sessions_processed}")
    print(f"  ✅ Successful Resolves:     {total_sessions_success}")
    if proxy_count > 0:
        print(f"  🌐 Proxies Available:       {proxy_count}")
        if used_enhanced_pool:
            stats = proxy_list.get_stats()
            print(f"  📈 Avg Proxy Score:         {stats['avg_score']:.3f}")
            print(f"  ⚡ Avg Proxy Latency:       {stats['avg_latency_ms']:.0f} ms")
            print(f"  🏠 Residential Proxies:     {stats['residential_proxies']}")
            print(f"  🏢 Datacenter Proxies:      {stats['datacenter_proxies']}")
    print(f"  ⏱️ Total Session Time:      {total_elapsed}s")
    print("=" * 60 + "\n")

def main():
    parser = argparse.ArgumentParser(
        prog="link",
        description="🌐 Universal Stealth Link Resolver & Multi-Shortener Bypasser in Termux.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage Examples:
  link                          # Single run on links.txt
  link -c 3                     # Parallel processing (3 links / 3 proxies simultaneously)
  link --loop -c 3              # 🔄 Automated INFINITE loop across all valid proxies
  link --rounds 10 -c 3         # 🔄 Run 10 consecutive rounds across proxies
  link --loop -c 2 --delay 10   # Infinite loop with 10s natural delay between cycles
  link -c 2 -o resolved.txt     # Log destinations to resolved.txt
  link --clean                  # Auto-prune dead/expired links from links.txt
  link --fetch-proxies          # Harvest 10,000+ proxies from hide.mn & global feeds
  link -P                       # Auto-scrape live proxies + stealth resolution
        """
    )
    parser.add_argument("target", nargs="?", default=None,
                        help="URL or text file with links (default: links.txt if present)")
    print("DEBUG: args added")
    parser.add_argument("-f", "--file", metavar="FILE", default=None,
                        help="Input file containing links (one per line)")
    parser.add_argument("-c", "--concurrency", type=int, default=3,
                        metavar="N", help="Number of concurrent parallel threads (default: 3, max: 4)")
    parser.add_argument("--loop", action="store_true",
                        help="Run indefinitely in an automated infinite loop across proxies (default behavior)")
    parser.add_argument("--once", action="store_true",
                        help="Run only 1 single round and exit")
    parser.add_argument("--rounds", type=int, default=None,
                        metavar="NUM", help="Number of proxy rotation rounds to run (default: continuous loop)")
    parser.add_argument("--delay", type=int, default=2,
                        metavar="SEC", help="Delay in seconds between consecutive rounds (default: 2)")
    parser.add_argument("--min-proxies", type=int, default=8,
                        metavar="NUM", help="Minimum healthy proxies before auto-refill triggers (default: 8)")
    parser.add_argument("-o", "--output", metavar="FILE", default="impressions.txt",
                        help="Save resolved final destinations to an output file (default: impressions.txt)")
    parser.add_argument("--clean", action="store_true",
                        help="Auto-prune dead/expired links from input links file")
    parser.add_argument("--fetch-proxies", action="store_true",
                        help="Harvest thousands of fresh proxies from hide.mn & live feeds into proxies_raw.txt")
    parser.add_argument("-P", "--public-proxies", action="store_true",
                        help="Auto-scrape live public proxies and validate them before running")
    parser.add_argument("--save-proxies", action="store_true",
                        help="Save validated live proxies to proxies.txt")
    parser.add_argument("--proxy-limit", type=int, default=10,
                        metavar="NUM", help="Number of valid public proxies to find (default: 10)")
    parser.add_argument("-p", "--proxy", metavar="PROXY", default=None,
                        help="Single proxy URL (e.g., http://user:pass@ip:port or socks5://ip:port)")
    parser.add_argument("--proxy-file", metavar="FILE", default="proxies.txt",
                        help="Custom proxies file path (default: proxies.txt)")
    parser.add_argument("--timeout", type=int, default=12,
                        metavar="SEC", help="Page timeout in seconds (default: 12)")
    parser.add_argument("--device", choices=["auto", "android", "ios", "mobile", "desktop"], default="auto",
                        help="Target device fingerprint emulation (default: auto [70%% Android, 25%% iOS, 5%% Desktop])")
    parser.add_argument("--dwell", type=int, default=0, metavar="SEC",
                        help="Post-destination dwell time in seconds for high-quality ad impression registration (e.g. 15-30s)")
    parser.add_argument("--full-ouo-validation", action="store_true",
                        help="Enable full OUO flow validation for proxies (tests actual JavaScript resolution, slower but more accurate)")

    args = parser.parse_args()
    print("DEBUG: args parsed")

    if args.fetch_proxies:
        if fetch_all_raw_proxies:
            target = args.proxy_file
            if target == "proxies.txt":
                # Save raw proxies to proxies_raw.txt so proxies.txt is not overwritten with unverified proxies
                target = os.path.join(SCRIPT_DIR, "proxies_raw.txt")
            all_p = fetch_all_raw_proxies()
            with open(target, "w", encoding="utf-8") as pf:
                for pr in all_p:
                    pf.write(f"{pr}\n")
            print(f"💾 Successfully harvested and saved {len(all_p)} proxies into: {target}\n")
            sys.exit(0)
        else:
            print("❌ proxy_manager module not found.")
            sys.exit(1)

    input_file = None
    urls = []

    if args.file:
        input_file = args.file
    elif args.target and (args.target.endswith(".txt") or os.path.isfile(args.target)):
        input_file = args.target
    elif args.target:
        urls = [args.target]
    else:
        if os.path.isfile("links.txt"):
            input_file = "links.txt"
        elif os.path.isfile(os.path.join(SCRIPT_DIR, "links.txt")):
            input_file = os.path.join(SCRIPT_DIR, "links.txt")
        else:
            parser.print_help()
            print("\n❌ Error: No URL provided and 'links.txt' not found.")
            sys.exit(1)

    if input_file:
        if not os.path.isfile(input_file):
            print(f"❌ Error: File '{input_file}' not found.")
            sys.exit(1)
        with open(input_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

    if not urls:
        print(f"⚠️ No valid links found to process in '{input_file}'.")
        sys.exit(0)

    print("DEBUG: About to load proxies")
    proxies = load_proxies(
        proxy_arg=args.proxy,
        proxy_file=args.proxy_file,
        use_public=args.public_proxies or args.save_proxies,
        public_limit=args.proxy_limit,
        validate_each=not args.public_proxies,  # skip for public (already validated by scrape_and_validate)
        validate_upfront=False,  # lazy validation: test one-by-one at use time
        min_threshold=args.min_proxies,
        target_size=args.min_proxies + 10,
        enable_ouo_validation=args.full_ouo_validation,
    )
    print(f"DEBUG: Proxies loaded: {type(proxies)}")

    # Check if using enhanced proxy pool
    used_enhanced_pool = USE_ENHANCED_PROXY and EnhancedProxyPool and isinstance(proxies, EnhancedProxyPool)
    used_dynamic_pool = (not USE_ENHANCED_PROXY) and DynamicProxyPool and isinstance(proxies, DynamicProxyPool)

    if args.save_proxies and proxies:
        save_path = os.path.join(SCRIPT_DIR, "proxies.txt")
        with open(save_path, "w", encoding="utf-8") as pf:
            if used_enhanced_pool:
                # Save active proxies from enhanced pool
                active = proxy_list.active_proxies
                for pr in active:
                    pf.write(f"{pr.server}\n")
                print(f"💾 Saved {len(active)} validated proxies to: {save_path}\n")
            elif DynamicProxyPool and isinstance(proxies, DynamicProxyPool):
                # Get all active proxies from pool
                active = list(proxies.active_proxies)
                for pr in active:
                    pf.write(f"{pr['server']}\n")
                print(f"💾 Saved {len(active)} validated proxies to: {save_path}\n")
            elif isinstance(proxies, list):
                for pr in proxies:
                    server = pr.server if hasattr(pr, 'server') else pr.get('server', '')
                    pf.write(f"{server}\n")
                print(f"💾 Saved {len(proxies)} validated proxies to: {save_path}\n")

    if args.once:
        is_loop = False
        rounds_count = 1
    elif args.rounds:
        is_loop = False
        rounds_count = args.rounds
    else:
        is_loop = True
        rounds_count = 1

    process_links(
        urls,
        proxies=proxies,
        concurrency=args.concurrency,
        timeout_sec=args.timeout,
        output_file=args.output,
        clean_input_file=input_file if args.clean else None,
        loop=is_loop,
        rounds=rounds_count,
        delay_sec=args.delay,
        min_proxies=args.min_proxies,
        device_type=args.device,
        dwell_sec=args.dwell
    )

if __name__ == "__main__":
    # Launch TUI if no arguments provided
    if len(sys.argv) == 1:
        try:
            from tui import main as tui_main
            tui_main()
        except ImportError:
            print("❌ TUI not available (rich not installed)")
            sys.exit(1)
    else:
        main()
