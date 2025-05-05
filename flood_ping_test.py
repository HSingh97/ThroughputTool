
import subprocess
import threading
import time
import re
import select
import psutil

from cleanup_utils import kill_ping

class FloodPingTest:
    def __init__(self, remote_ip, iface, packet_size, profile, latency_limit, loss_limit, ui_refs, data_store, graph, update_metrics=None):
        self.remote_ip = remote_ip
        self.iface = iface
        self.packet_size = packet_size
        self.profile = profile
        self.latency_limit = latency_limit
        self.loss_limit = loss_limit
        self.ui = ui_refs
        self.data = data_store
        self.graph = graph
        self.update_metrics = update_metrics
        self.stop_flag = False
        self.background_pids = []
        self.thread = threading.Thread(target=self._run_test, daemon=True)

    def run(self):
        self.thread.start()

    def stop(self):
        self.stop_flag = True
        kill_ping()
        self.background_pids.clear()

    def _run_test(self):
        profile_steps = {"Safe": 1, "Moderate": 5, "Aggressive": 10}
        step = profile_steps.get(self.profile, 5)
        instance_id = 0
        start_time = time.time()
        plateau_window = []

        self._log(f"\n=========================== Test Started at {time.strftime('%H:%M:%S')} ===========================\n")

        while not self.stop_flag:
            batch = []
            for _ in range(step):
                proc = subprocess.Popen(["sudo", "ping", "-f", "-s", str(self.packet_size), self.remote_ip],
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                batch.append(proc.pid)
                self.background_pids.append(proc.pid)

            instance_id += len(batch)

            ping_proc = subprocess.Popen(
                ["ping", "-i", "0.01", "-s", str(self.packet_size), "-c", "50", self.remote_ip],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            )
            ping_output, _ = ping_proc.communicate()

            loss_match = re.search(r"(\d+)% packet loss", ping_output)
            rtt_match = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", ping_output)

            icmp_loss = float(loss_match.group(1)) if loss_match else 0.0
            icmp_latency = float(rtt_match.group(1)) if rtt_match else 999.0
            duration = int(time.time() - start_time)

            current_tx = self._get_if_rate('tx')
            current_rx = self._get_if_rate('rx')
            total = current_tx + current_rx

            self.data["tx"].append(current_tx)
            self.data["rx"].append(current_rx)
            self.data["latency"].append(icmp_latency)
            self.data["throughput"].append(total)
            self.data["timestamp"].append(time.time())

            self.graph.update_graphs(self.data["timestamp"], self.data["tx"], self.data["rx"], self.data["latency"])

            if self.update_metrics:
                self.update_metrics(tx=current_tx, rx=current_rx, latency=icmp_latency, loss=icmp_loss, duration_secs=duration, total=total)

            self._log(
                f"[+] Instance {instance_id:<4} | Tx: {current_tx:7.2f} Mbps | Rx: {current_rx:7.2f} Mbps | "
                f"Latency: {icmp_latency:6.2f} ms | Loss: {icmp_loss:5.1f}%"
            )

            if icmp_latency > self.latency_limit:
                self._abort(f"Latency {icmp_latency:.2f} ms exceeded {self.latency_limit} ms", start_time)
                return

            if icmp_loss > self.loss_limit:
                self._abort(f"ICMP Loss {icmp_loss:.2f}% exceeded {self.loss_limit}%", start_time)
                return

            plateau_window.append(total)
            if len(plateau_window) > 4:
                plateau_window.pop(0)
                base = sum(plateau_window) / len(plateau_window)
                delta = max(plateau_window) - min(plateau_window)
                if base > 0 and (delta / base * 100) < 5:
                    self._abort("Throughput plateau detected over 4 batches.", start_time)
                    return

            time.sleep(1.5)

        self._final_summary(start_time)

    def _abort(self, reason, start_time):
        self._log(f"[!] {reason}")
        self.stop_flag = True
        kill_ping()
        self._final_summary(start_time)

    def _final_summary(self, start_time):
        duration = int(time.time() - start_time)
        mins, secs = divmod(duration, 60)
        final_tx = self.data['tx'][-1] if self.data['tx'] else 0.0
        final_rx = self.data['rx'][-1] if self.data['rx'] else 0.0
        total = final_tx + final_rx

        self._log("\n--- Flood Ping Test Stopped ---")
        self._log(f"Test Duration        : {mins:02}:{secs:02} (mm:ss)")
        self._log(f"Final Live Tx        : {final_tx:.2f} Mbps")
        self._log(f"Final Live Rx        : {final_rx:.2f} Mbps")
        self._log(f"Total Throughput     : {total:.2f} Mbps")

        self.ui['start_button'].config(state="normal")
        self.ui['stop_button'].config(state="disabled")
        self.ui['status_bar'].config(text="Test Stopped")
        # Explicit enable buttons
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

    def _get_if_rate(self, direction):
        stats1 = psutil.net_io_counters(pernic=True)[self.iface]
        time.sleep(0.3)
        stats2 = psutil.net_io_counters(pernic=True)[self.iface]
        if direction == 'tx':
            rate = (stats2.bytes_sent - stats1.bytes_sent) * 8 / 1e6
        else:
            rate = (stats2.bytes_recv - stats1.bytes_recv) * 8 / 1e6
        return round(rate, 2)