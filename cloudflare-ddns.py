#!/usr/bin/env python3
"""Cloudflare DDNS Client

Summary: Access your home network remotely via a custom domain name without a static IP!
Description:    Access your home network remotely via a custom domain
                A small, 🕵️ privacy centric, and ⚡ lightning fast
                multi-architecture Docker image for self hosting projects.

                Updates A/AAAA DNS records via the Cloudflare API with proper upsert,
                retries/backoff, TTL "auto" handling (maps to 1), dry-run, and optional
                purging of old records per FQDN/type. Supports IPv4/IPv6 and optional load
                balancer updates. Configuration via `config.json` and `CF_DDNS_*` envs.
"""
import json
import os
import signal
import sys
import threading
import time
import ipaddress
import urllib.request
import urllib.error
from string import Template
from urllib.parse import urlparse

__version__ = "1.0.3"

CONFIG_PATH = os.environ.get("CONFIG_PATH", os.getcwd())
ENV_VARS = {key: value for (key, value) in os.environ.items() if key.startswith("CF_DDNS_")}


class GracefulExit:
    """Graceful exit helper to stop the main loop cleanly."""

    def __init__(self):
        """Register signal handlers and set up state."""
        self.kill_now = threading.Event()
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        """Gracefully request exit after the current cycle.

        Args:
            signum (int): Signal number (e.g., SIGINT).
            frame (FrameType): Current stack frame when the signal arrived.
        """
        print(
            "\n🛑 Stopping main thread after the cycle (1 Cycle is chosen by ttl from config.json) or after the sleep cycle...\n",
            flush=True,
        )
        self.kill_now.set()


class CloudflareDDNS:
    """Cloudflare DDNS updater.

    Args:
        killer (GracefulExit | None): Controller to allow graceful stop.
        dry_run (bool): If True, only print intended changes without API calls.
    """

    def __init__(self, killer: GracefulExit = None, dry_run: bool = False):
        """Initialize updater state.

        Args:
            killer (GracefulExit | None): Controller to allow graceful stop.
            dry_run (bool): If True, only print intended changes without API calls.
        """
        self.print_header(f"📡 Starting cloudflare-ddns v{__version__}")
        self.config = None
        self.ttl = 300
        self.killer = killer
        self.dry_run = dry_run
        self.warnings = {
            "ipv4": False,
            "ipv6": False,
            "ipv4_secondary": False,
            "ipv6_secondary": False,
        }
        self.purge_unknown_records = False
        self.ipv4_enabled = False
        self.ipv6_enabled = False
        self.cyclic_config_read = False
        self.ipv4_endpoints = (
            "https://1.1.1.1/cdn-cgi/trace",
            "https://ipv4.icanhazip.com",
        )
        self.ipv6_endpoints = (
            "https://[2606:4700:4700::1111]/cdn-cgi/trace",
            "https://ipv6.icanhazip.com",
        )
        self.load_config()

    def load_config(self):
        """Load the configuration file with records to update."""
        try:
            with open(os.path.join(CONFIG_PATH, "config.json")) as config_file:
                self.config = (
                    json.loads(Template(config_file.read()).safe_substitute(ENV_VARS))
                    if ENV_VARS
                    else json.loads(config_file.read())
                )
        except Exception as e:
            self.print_flush(f"😡 Error reading config.json {e}")
            time.sleep(self.config.get("sleep_time", 20) if self.config else 20)
            (self.load_config() if self.cyclic_config_read else self.killer.kill_now.set())

    def parse_config(self):
        """Parse the configuration file.
        Raises:
            Exception: If the config was not found.
        """
        if not self.config:
            raise Exception("Config not found")

        # TTL Configuration (supports "auto")
        raw_ttl = self.config.get("ttl", 300)
        if isinstance(raw_ttl, str) and raw_ttl.strip().lower() == "auto":
            self.ttl = 1  # Cloudflare API: 1 represents Auto
            self.print_flush("🕰️  Updating records with TTL: Auto (1)")
        else:
            try:
                self.ttl = max(60, int(raw_ttl))
                if int(raw_ttl) < self.ttl:
                    self.print_flush(f"⚙️  TTL {raw_ttl} is too low - defaulting to 60 seconds (5 min update)")
                else:
                    self.print_flush(f"🕰️  Updating records with {self.ttl} ttl.")
            except Exception:
                self.ttl = 300
                self.print_flush(f"⚙️  Invalid ttl value '{raw_ttl}' - defaulting to 300 seconds")
        self.print_flush(f"⚙️  To change the duration of the update, change the ttl in the configuration file: config.json\n")

        # IPv4 Configuration
        self.ipv4_enabled = self.config.get("a", True)
        if self.ipv4_enabled:
            self.print_flush(f"🕰️  Updating IPv4 (A) records with {self.ttl} ttl.")
        else:
            self.print_flush(f"⚙️  Updating IPv4 (A) is disabled")

        # IPv6 Configuration
        self.ipv6_enabled = self.config.get("aaaa", True)
        if self.ipv6_enabled:
            self.print_flush(f"🕰️  Updating IPv6 (AAAA) records with {self.ttl} ttl.")
        else:
            self.print_flush(f"⚙️  Updating IPv6 (AAAA) is disabled")

        # Purge Configuration
        self.purge_unknown_records = self.config.get("purgeUnknownRecords", False)
        if self.purge_unknown_records:
            self.print_flush(f"🗑️  Purging unknown records is enabled.")
        else:
            self.print_flush(f"⚙️  Purging unknown records is disabled")

        # Cyclic Config Read Configuration
        self.cyclic_config_read = self.config.get("cyclic_config_read", False)
        if self.cyclic_config_read:
            self.print_flush(f"🔁 Cyclic config read is enabled.")
            self.print_flush(
                f"⚙️  The duration of the update depends on the ttl and sleep_time in the configuration file: config.json"
            )
        else:
            self.print_flush(f"⚙️  Cyclic config read is disabled")

        self.sleep_time = self.config.get("sleep_time", 20)
        self.print_flush("")  # Empty line for better formatting

    def _purge_other_records_for_name(self, zone_id: str, record_type: str, fqdn: str, keep_id: str, option: dict):
        """Delete old records of the same name and type, except the managed one.

        Args:
            zone_id (str): Cloudflare zone id.
            record_type (str): "A" or "AAAA".
            fqdn (str): Fully qualified domain name.
            keep_id (str): Record id to keep.
            option (dict): Config object (auth and zone).
        Returns:
            None
        """
        if not self.purge_unknown_records:
            return

        query = f"zones/{zone_id}/dns_records?per_page=100&type={record_type}&name={fqdn}"
        records = self.cf_api(query, "GET", option)
        if not records or not records.get("result"):
            return
        for record in records["result"]:
            if record.get("id") == keep_id:
                continue
            if self.dry_run:
                self.print_flush(
                    f"🗑️  [Dry-Run] Would delete old {record_type} record for {fqdn}: {record.get('content')} (id={record.get('id')})"
                )
                continue
            self.cf_api(f"zones/{zone_id}/dns_records/{record['id']}", "DELETE", option)
            self.print_flush(f"🗑️  Deleted old {record_type} record for {fqdn}: {record.get('content')} (id={record.get('id')})")

    def get_ip(self, endpoint, ip_version: str = None):
        """Fetch the public IP address from a given endpoint.

        Args:
            endpoint (str): URL to query (e.g., Cloudflare trace or icanhazip).
            ip_version (str | None): "ipv4" or "ipv6" to validate the returned IP version.

        Raises:
            urllib.error.HTTPError: If the HTTP request fails with an HTTP error.
            urllib.error.URLError: If there is a network/URL error.
            ValueError: If the response does not contain a valid IP or the version does not match.

        Returns:
            str: The detected public IP address.
        """
        self.print_flush(f"🕰️  Getting IP from {endpoint}")
        req = urllib.request.Request(
            endpoint,
            headers={"User-Agent": f"cloudflare-ddns/{__version__}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        lines = [line for line in text.split("\n") if line.strip()]
        ip_str = (dict(i.split("=") for i in lines)["ip"] if len(lines) > 1 else lines[0]).strip()
        try:
            parsed = ipaddress.ip_address(ip_str)
            if ip_version == "ipv4" and parsed.version != 4:
                raise ValueError("Expected IPv4 address")
            if ip_version == "ipv6" and parsed.version != 6:
                raise ValueError("Expected IPv6 address")
        except Exception as e:
            raise ValueError(f"Invalid IP '{ip_str}': {e}")
        return ip_str

    def get_ips(self):
        """Get the public IP addresses from the configured endpoints.
        Returns:
            dict: The IP addresses.
        """
        ipv4, ipv6 = None, None
        if self.ipv4_enabled:
            ipv4 = self.try_get_ip(self.ipv4_endpoints, "ipv4")
        if self.ipv6_enabled:
            ipv6 = self.try_get_ip(self.ipv6_endpoints, "ipv6")
        return {
            "ipv4": {"type": "A", "ip": ipv4} if ipv4 else None,
            "ipv6": {"type": "AAAA", "ip": ipv6} if ipv6 else None,
        }

    def try_get_ip(self, endpoints, ip_version):
        """Try to get the public IP address from the configured endpoints.
        Args:
            endpoints (list): The endpoints to try to get the IP address from.
            ip_version (str): The IP version to try to get the IP address from.
        Returns:
            dict: The IP address.
        """
        for i, endpoint in enumerate(endpoints):
            try:
                return self.get_ip(endpoint, ip_version)
            except Exception as e:
                key = ip_version if i == 0 else f"{ip_version}_secondary"
                if not self.warnings.get(key, False):
                    self.warnings[key] = True
                    if i != len(endpoints) - 1:
                        self.print_flush(
                            f"🧩 {ip_version.upper()} not detected via {urlparse(endpoint).netloc}, trying {urlparse(endpoints[i+1]).netloc}"
                        )
                    else:
                        self.print_flush(f"\n🛑 Error:")
                        self.print_flush(f"🧩 {ip_version.upper()} could not be detected via {urlparse(endpoint).netloc}.")
                        self.print_flush("   Verify that your default gateway is set correctly and your ISP and/or DNS provider isn't blocking Cloudflare IPs.")
                        self.print_flush(f"   Error: {e}")
        return None

    def update_ips(self):
        """Update the Cloudflare DNS records with the public IP addresses."""
        self.load_config() if self.cyclic_config_read else None
        self.parse_config()
        for ip in filter(None, self.get_ips().values()):
            self.commit_record(ip)
            if self.config.get("load_balancer"):
                self.update_load_balancer(ip)

    def commit_record(self, ip):
        """Commit the record to Cloudflare.
        Args:
            ip (dict): The IP address to commit.
        """
        for option in self.config.get("cloudflare", []):
            subdomains = option.get("subdomains", [])
            zone_response = self.cf_api(f"zones/{option['zone_id']}", "GET", option)
            if not zone_response or not zone_response.get("result"):
                self.print_flush(f"😡 Failed to fetch zone information for zone_id: {option['zone_id']}")
                continue
            base_domain_name = zone_response["result"]["name"]
            self.print_flush(f"🔍 Base domain name: {base_domain_name}")

            for subdomain in subdomains:
                sub_domain = subdomain["name"] if subdomain["name"] not in ("", "@") else ""
                fqdn = base_domain_name if sub_domain in ("", "@") else f"{sub_domain}.{base_domain_name}"
                desired_proxied = subdomain.get("proxied", False)
                desired_ttl = 1 if desired_proxied else self.ttl

                # Lookup existing record for upsert
                lookup = self.cf_api(
                    f"zones/{option['zone_id']}/dns_records?type={ip['type']}&name={fqdn}&per_page=100",
                    "GET",
                    option,
                )
                existing = None
                if lookup and lookup.get("result"):
                    existing = lookup["result"][0]

                if existing and (
                    existing.get("content") == ip["ip"]
                    and bool(existing.get("proxied")) == bool(desired_proxied)
                    and int(existing.get("ttl", 1)) == int(desired_ttl)
                ):
                    self.print_flush(f"⏭️  No changes for {fqdn} ({ip['type']})")
                    record_id = existing.get("id")
                else:
                    payload = {
                        "type": ip["type"],
                        "content": ip["ip"],
                        "proxied": desired_proxied,
                        "ttl": desired_ttl,
                        "name": fqdn,
                    }
                    if self.dry_run:
                        action = "update" if existing else "create"
                        self.print_flush(f"📡 [Dry-Run] Would {action} {fqdn}: {payload}")
                        record_id = existing.get("id") if existing else None
                    else:
                        if existing:
                            self.print_flush(f"📡 Updating existing record for {fqdn}: {payload}")
                            resp = self.cf_api(
                                f"zones/{option['zone_id']}/dns_records/{existing['id']}",
                                "PATCH",
                                option,
                                {},
                                payload,
                            )
                            record_id = (
                                existing.get("id")
                                if not resp or not resp.get("result")
                                else resp["result"].get("id")
                            )
                        else:
                            self.print_flush(f"📡 Creating new record for {fqdn}: {payload}")
                            resp = self.cf_api(
                                f"zones/{option['zone_id']}/dns_records",
                                "POST",
                                option,
                                {},
                                payload,
                            )
                            record_id = None if not resp else resp.get("result", {}).get("id")

                    if not self.dry_run and not record_id:
                        self.print_flush(f"😡 Failed to create/update DNS record for {fqdn}")
                    else:
                        self.print_flush(f"✅ DNS record processed successfully for {fqdn}")

                # Purge old records for this name/type if enabled
                if existing or not self.dry_run:
                    keep_id = existing.get("id") if existing else (record_id or None)
                else:
                    keep_id = None
                if keep_id:
                    self._purge_other_records_for_name(option["zone_id"], ip["type"], fqdn, keep_id, option)

                self.print_flush("")  # Empty line for better formatting
            self.print_flush("")  # Empty line for better formatting

    def update_load_balancer(self, ip):
        """Update Cloudflare Load Balancer IP addresses.
        I am not sure if it works or not. It was in the original script but was commented out so i kept it here.
        with the condition of the load balancer being enabled in the config.
        Args:
            ip (dict): The IP address to update.
        """
        for option in self.config.get("load_balancer", []):
            pools = self.cf_api("user/load_balancers/pools", "GET", option)
            if not pools or "result" not in pools:
                self.print_flush(f"😡 Failed to fetch Load Balancer pools")
                continue

            # find next suitable pool
            pool = next((p for p in pools["result"] if p.get("id") == option.get("pool_id")), None)
            if pool is None:
                continue

            origins = pool.get("origins", [])

            # find next suitable origin
            origin = next((o for o in origins if o.get("name") == option.get("origin")), None)
            if origin is None:
                continue

            origin["address"] = ip.get("ip")
            data = {"origins": origins}
            response = self.cf_api(f'user/load_balancers/pools/{option["pool_id"]}', "PATCH", option, {}, data)

    def cf_api(self, endpoint, method, config, headers={}, data=None):
        """Execute a Cloudflare API request using urllib with manual retries.

        Args:
            endpoint (str): API endpoint relative to /client/v4/.
            method (str): HTTP method (GET/POST/PATCH/DELETE/...).
            config (dict): Config containing auth information.
            headers (dict, optional): Extra headers to merge into the request.
            data (dict, optional): JSON body for the request (for POST/PUT/PATCH).

        Returns:
            dict | None: Parsed JSON payload on success, or None on error.
        """
        auth_headers = (
            {"Authorization": f"Bearer {config['authentication']['api_token']}"}
            if config["authentication"].get("api_token")
            else {
                "X-Auth-Email": config["authentication"]["api_key"].get("account_email", ""),
                "X-Auth-Key": config["authentication"]["api_key"].get("api_key", ""),
            }
        )
        base_headers = {
            "Accept": "application/json",
            "User-Agent": f"cloudflare-ddns/{__version__}",
        }
        body_bytes = None
        if data is not None and method.upper() in {"POST", "PUT", "PATCH"}:
            body_bytes = json.dumps(data).encode("utf-8")
            base_headers["Content-Type"] = "application/json"
        merged_headers = {**base_headers, **auth_headers, **(headers or {})}

        url = f"https://api.cloudflare.com/client/v4/{endpoint}"
        max_retries = 5
        backoff = 1
        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(url, data=body_bytes, headers=merged_headers, method=method)
                with urllib.request.urlopen(req, timeout=15) as resp:
                    resp_text = resp.read().decode("utf-8", errors="replace")
                    payload = json.loads(resp_text) if resp_text else None
                    if isinstance(payload, dict) and payload.get("success") is False:
                        raise Exception(f"Cloudflare API error in {method} {endpoint}: {payload.get('errors')}")
                    return payload
            except urllib.error.HTTPError as e:
                status = getattr(e, "code", None)
                try:
                    err_text = e.read().decode("utf-8", errors="replace")
                    err_payload = json.loads(err_text) if err_text else None
                except Exception:
                    err_payload = {"raw": err_text if "err_text" in locals() else ""}
                if status in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    retry_after = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                    if retry_after:
                        try:
                            sleep_s = int(retry_after)
                        except Exception:
                            sleep_s = backoff
                    else:
                        sleep_s = backoff
                    self.print_flush(f"⏳ Retrying {method} {endpoint} after HTTP {status} in {sleep_s}s ...")
                    time.sleep(sleep_s)
                    backoff *= 2
                    continue
                if status in (401, 403):
                    self.print_flush("\n😡 Please check your api_token in the config.json file.")
                self.print_flush(f"HTTP {status} in {method} {endpoint}: {err_payload}")
                return None
            except urllib.error.URLError as e:
                if attempt < max_retries - 1:
                    self.print_flush(f"Network error in {method} {endpoint}: {e}. Retrying in {backoff}s...")
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                self.print_flush(f"Network error in {method} {endpoint}: {e}")
                return None
            except Exception as e:
                self.print_flush(f"{e}\n")
                return None

    def print_header(self, title):
        """Print a formatted header with the given title.
        Args:
            title (str): The title to print.
        """
        len_title = len(title) + 5
        self.print_flush(f"\n{'#' * len_title}\n# {title} #\n{'#' * len_title}\n")

    def print_flush(self, message):
        """Print a message and flush the output.
        Args:
            message (str): The message to print.
        """
        print(message, flush=True)


if __name__ == "__main__":
    if sys.version_info < (3, 5):
        raise Exception("🐍 This script requires Python 3.5+")

    args = set(sys.argv[1:])
    repeat_flags = ["--repeat", "-repeat", "repeat", "--r", "-r", "r", "--loop", "-loop", "loop", "--l", "-l", "l"]
    repeat = any(flag in args for flag in repeat_flags)
    dry_run_flags = ["--dry-run", "--dryrun", "--dr", "-dryrun", "-dr", "dryrun", "dr"]
    dry_run = any(flag in args for flag in dry_run_flags)

    killer = GracefulExit()
    ddns = CloudflareDDNS(killer, dry_run=dry_run)

    if repeat:
        while not killer.kill_now.is_set():
            ddns.update_ips()
            # Choose a conservative wait time: respect larger of sleep_time and ttl (ignore ttl=1/auto)
            effective_ttl = 0 if ddns.ttl == 1 else ddns.ttl
            wait_time = max(ddns.sleep_time, effective_ttl)
            ddns.print_flush(f"⏲️  Waiting {wait_time} seconds to avoid rate limiting")
            ddns.print_flush(f"⏲️  Waiting {wait_time} seconds, because:")
            ddns.print_flush(f"    - the ttl is {ddns.ttl} seconds")
            ddns.print_flush(f"    - the sleep time is {ddns.sleep_time} seconds")
            ddns.print_flush("")  # Empty line before next cycle
            time.sleep(wait_time) if not killer.kill_now.is_set() else exit(0)
    elif not killer.kill_now.is_set():
        if args:
            unknown = " ".join(sorted(args - {"--repeat", "--dry-run"}))
            if unknown:
                ddns.print_flush(f"❓ Unrecognized parameter(s) '{unknown}'.")
        else:
            ddns.print_flush(f"💡 Usage to run it in loop: python -u {sys.argv[0].split('/')[-1]} --repeat [--dry-run]")
        ddns.print_flush(f"🕰️  Trying to update records 1 time{' (dry-run)' if dry_run else ''}...")
        ddns.update_ips()
        time.sleep(ddns.config.get("sleep_time") if ddns.config else 10)
