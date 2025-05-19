import subprocess
import threading
import time
import psutil
import netifaces


class L2TrafficTest:
    def __init__(self, iface, ui_refs, ethertype="All", remote_mac="", packet_size=1400, profile="Moderate",
                 data_store=None, graph=None, update_metrics=None):
        self.iface = iface
        self.ui = ui_refs
        self.ethertype = ethertype
        self.remote_mac = remote_mac.strip()
        self.packet_size = packet_size
        self.profile = profile
        self.data = data_store or {"tx": [], "rx": [], "latency": [], "timestamp": [], "throughput": []}
        self.graph = graph
        ethertype_map = {
            "IPv4": "0x0800",
            "ARP": "0x0806",
            "IPv6": "0x86DD",
            "VLAN": "0x8100",
            "MPLS": "0x8847",
            "PPPoE": "0x8864",
            "Loopback": "0x9000",
            "Unknown": "0x88B5",
            "0xFFFF": "0xFFFF"
        }
        self.ethertype_code = ethertype_map.get(self.ethertype, "0x0800")

        self.update_metrics = update_metrics
        self.stop_flag = False
        self.process = None
        self.thread = threading.Thread(target=self._run_test, daemon=True)

    def run(self):
        self.thread.start()

    def stop(self):
        self.stop_flag = True
        try:
            subprocess.run(["sudo", "pkill", "-f", "./l2_flooder"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except Exception as e:
            self._log(f"[!] Failed to stop flooder: {e}")

    def _get_iface_ip(self):
        try:
            import netifaces
            return netifaces.ifaddresses(self.iface)[netifaces.AF_INET][0]['addr']
        except Exception as e:
            self._log(f"[!] Failed to get local IP: {e}")
            return "0.0.0.0"

    def _get_iface_mac(self):
        try:
            return netifaces.ifaddresses(self.iface)[netifaces.AF_LINK][0]['addr']
        except Exception as e:
            self._log(f"[!] Failed to get MAC: {e}")
            return "00:00:00:00:00:00"

    def _run_test(self):
        self._log("\n=== L2/L3 Traffic Test Started ===\n")
        start_time = time.time()

        dst_mac = self.remote_mac if self.remote_mac else "ff:ff:ff:ff:ff:ff"

        # Build ethertype value (e.g. "0x0800")
        ethertype_map = {
            "IPv4": "0x0800",
            "ARP": "0x0806",
            "IPv6": "0x86DD",
            "VLAN": "0x8100",
            "MPLS": "0x8847",
            "PPPoE": "0x8864",
            "Loopback": "0x9000",
            "Unknown": "0x88B5",
            "0xFFFF": "0xFFFF"
        }
        ethertype_hex = ethertype_map.get(self.ethertype, "0x0800")

        cmd = ["sudo", "./l2_flooder", self.iface, str(self.packet_size), ethertype_hex, dst_mac]

        self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        while not self.stop_flag:
            stats1 = psutil.net_io_counters(pernic=True)[self.iface]
            t1 = time.time()
            time.sleep(1)
            stats2 = psutil.net_io_counters(pernic=True)[self.iface]
            t2 = time.time()

            rx_rate = (stats2.bytes_recv - stats1.bytes_recv) * 8 / (t2 - t1) / 1e6  # Mbps
            duration = int(t2 - start_time)

            self.data["tx"].append(0.0)  # Tx ignored
            self.data["rx"].append(rx_rate)
            self.data["latency"].append(0.0)
            self.data["throughput"].append(rx_rate)  # Use Rx as throughput
            self.data["timestamp"].append(time.time())

            if self.update_metrics:
                self.update_metrics(tx=0.0, rx=rx_rate, latency=0.0, loss=0.0, duration_secs=duration, total=rx_rate)

            self.graph.update_graphs(
                self.data["timestamp"],
                self.data["tx"],
                self.data["rx"],
                self.data["latency"]
            )

            self._log(f"[+] Burst Sent | Rx: {rx_rate:.2f} Mbps")

        self._final_summary(start_time)

    def _final_summary(self, start_time):
        duration = int(time.time() - start_time)
        mins, secs = divmod(duration, 60)
        final_tx = self.data['tx'][-1] if self.data['tx'] else 0.0
        final_rx = self.data['rx'][-1] if self.data['rx'] else 0.0
        total = final_tx + final_rx

        self._log("\n--- L2/L3 Traffic Test Stopped ---")
        self._log(f"Test Duration        : {mins:02}:{secs:02} (mm:ss)")
        self._log(f"Final Live Tx        : {final_tx:.2f} Mbps")
        self._log(f"Final Live Rx        : {final_rx:.2f} Mbps")
        self._log(f"Total Throughput     : {total:.2f} Mbps")

        self.ui['start_button'].config(state="normal")
        self.ui['stop_button'].config(state="disabled")
        self.ui['status_bar'].config(text="Test Stopped")

        try:
            self.ui['export_log_btn'].config(state="normal")
            self.ui['save_tp_graph_btn'].config(state="normal")
            self.ui['save_latency_graph_btn'].config(state="normal")
        except Exception as e:
            self._log(f"[UI Error] Failed to enable buttons: {e}")

    def _log(self, msg):
        self.ui['output_area'].config(state='normal')
        self.ui['output_area'].insert("end", msg + "\n")
        self.ui['output_area'].config(state='disabled')
        self.ui['output_area'].see("end")