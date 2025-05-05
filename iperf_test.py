import subprocess
import threading
import json
import time
import psutil
import re
from cleanup_utils import kill_iperf


class IperfTest:
    def __init__(self, remote_ip, iface, protocol, direction, profile, ui_refs, data_store, graph, update_metrics=None):
        self.remote_ip = remote_ip
        self.iface = iface
        self.protocol = protocol
        self.direction = direction
        self.profile = profile
        self.ui = ui_refs
        self.data = data_store
        self.graph = graph
        self.update_metrics = update_metrics
        self.stop_flag = False
        self.latency_stop_flag = False
        self.latency_thread = None
        self.loss_limit = float(self.ui['entries']['Loss Threshold (%)'].get())
        self.thread = threading.Thread(target=self._run_test, daemon=True)

    def run(self):
        self.ui['export_log_btn'].config(state='disabled')
        self.ui['save_tp_graph_btn'].config(state='disabled')
        self.ui['save_latency_graph_btn'].config(state='disabled')
        self.thread.start()

    def stop(self):
        self.stop_flag = True
        self.latency_stop_flag = True
        kill_iperf()

    def _run_test(self):
        streams = 2
        current_bw = 10
        bw_step = {"Safe": 10, "Moderate": 30, "Aggressive": 50}.get(self.profile, 30)
        max_total_tp = 0.0
        no_gain_count = 0
        start_time = time.time()
        bw_freeze = False
        last_lossless_bw = current_bw
        loss_seen_once = False

        self._start_latency_monitor()

        self._log(
            f"\n======================= iPerf3 {self.protocol} Test Started at {time.strftime('%H:%M:%S')} =======================\n"
        )

        while not self.stop_flag:
            latency = self.data["latency"][-1] if self.data["latency"] else 0.0

            cmd = ["iperf3", "-c", self.remote_ip, "-t", "5", "-J", "-P", str(streams)]
            if self.protocol == "UDP":
                cmd += ["-u", "-b", f"{current_bw}M"]
            if self.direction == "Downlink":
                cmd += ["-R"]
            elif self.direction == "Bi-Di":
                cmd += ["--bidir"]

            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=20)
                output = proc.stdout
            except subprocess.TimeoutExpired:
                self._log("[!] iPerf3 test timed out.")
                break

            try:
                json_start = output.index('{')
                json_str = output[json_start:]
                result = json.loads(json_str)
            except Exception:
                self._stop_log("Failed to parse iperf3 JSON output", start_time, max_tp=max_total_tp)
                return

            if self.protocol == "UDP":
                sum_data = result.get("end", {}).get("sum", {})
                tx_mbps = sum_data.get("bits_per_second", 0) / 1e6
                lost = sum_data.get("lost_packets", 0)
                sent = sum_data.get("packets", 1)
                loss = (lost / sent * 100.0) if sent > 0 else 0.0
                delivered = tx_mbps * (1 - loss / 100.0)

                self._log(f"[+] Streams: {streams} | BW: {current_bw}M | Tx: {tx_mbps:.2f} Mbps | Delivered: {delivered:.2f} Mbps | Loss: {loss:.2f}%")

                timestamp = time.time()
                self.data["tx"].append(tx_mbps)
                self.data["rx"].append(0.0)
                self.data["throughput"].append(delivered)
                self.data["timestamp"].append(timestamp)

                if self.update_metrics:
                    self.update_metrics(tx=tx_mbps, rx=0.0, latency=latency,
                                        loss=loss, duration_secs=int(timestamp - start_time), total=delivered)
                self.graph.update_graphs(self.data["timestamp"], self.data["tx"], self.data["rx"], self.data["latency"])

                # Update max based on delivered only
                if loss == 0.0:
                    last_lossless_bw = current_bw
                    loss_seen_once = False
                    if delivered > max_total_tp:
                        max_total_tp = delivered
                        no_gain_count = 0
                    else:
                        no_gain_count += 1

                    if not bw_freeze:
                        current_bw += bw_step
                    else:
                        streams += 1

                elif loss > 0.0:
                    if not loss_seen_once:
                        loss_seen_once = True  # try once more
                    else:
                        current_bw = last_lossless_bw
                        bw_freeze = True
                        loss_seen_once = False
                        streams += 1
                        no_gain_count = 0

                if loss > self.loss_limit:
                    self._stop_log(f"Loss {loss:.2f}% > {self.loss_limit:.2f}%", start_time, max_tp=max_total_tp)
                    return

                if bw_freeze and no_gain_count >= 3:
                    self._stop_log("No gain after stream increase", start_time, max_tp=max_total_tp)
                    return

            else:
                # TCP (unchanged)
                sum_sent = result.get("end", {}).get("sum_sent", {})
                sum_received = result.get("end", {}).get("sum_received", {})
                if self.direction == "Uplink":
                    tx = sum_sent.get("bits_per_second", 0) / 1e6
                    rx = 0.0
                elif self.direction == "Downlink":
                    tx = 0.0
                    rx = sum_received.get("bits_per_second", 0) / 1e6
                else:
                    tx = sum_sent.get("bits_per_second", 0) / 1e6
                    rx = sum_received.get("bits_per_second", 0) / 1e6
                total = tx + rx
                self._log(f"[+] Streams: {streams:<2} | Tx: {tx:.2f} Mbps | Rx: {rx:.2f} Mbps | Total: {total:.2f} Mbps")

                timestamp = time.time()
                self.data["tx"].append(tx)
                self.data["rx"].append(rx)
                self.data["throughput"].append(total)
                self.data["timestamp"].append(timestamp)

                if self.update_metrics:
                    self.update_metrics(tx=tx, rx=rx, latency=latency,
                                        loss=0.0, duration_secs=int(timestamp - start_time), total=total)
                self.graph.update_graphs(self.data["timestamp"], self.data["tx"], self.data["rx"], self.data["latency"])

                if total > max_total_tp:
                    max_total_tp = total
                    no_gain_count = 0
                else:
                    no_gain_count += 1

                if no_gain_count >= 5:
                    self._stop_log("TCP throughput plateau detected", start_time, max_tp=max_total_tp)
                    return

                streams += 1

        self._final_summary(start_time, max_tp=max_total_tp)

    def _start_latency_monitor(self):
        def monitor():
            pattern = re.compile(r'time=([\d.]+)')
            while not self.latency_stop_flag and not self.stop_flag:
                try:
                    proc = subprocess.run(["ping", "-c", "1", self.remote_ip], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    output = proc.stdout
                    match = pattern.search(output)
                    latency = float(match.group(1)) if match else 0.0
                except:
                    latency = 0.0
                self.data["latency"].append(latency)
                time.sleep(1)

        self.latency_thread = threading.Thread(target=monitor, daemon=True)
        self.latency_thread.start()

    def _stop_log(self, reason, start_time, max_tp=0.0):
        self._log(f"\n-------- iPerf3 Test Stopped ({reason}) --------")
        self._final_summary(start_time, max_tp=max_tp)

    def _final_summary(self, start_time, stable_tp=None, max_tp=0.0):
        self.latency_stop_flag = True
        if self.latency_thread and self.latency_thread.is_alive():
            self.latency_thread.join()

        duration = int(time.time() - start_time)
        mins, secs = divmod(duration, 60)

        tx = self.data["tx"][-1] if self.data["tx"] else 0.0
        rx = self.data["rx"][-1] if self.data["rx"] else 0.0
        total = tx + rx

        self._log("\n-------------------- iPerf3 Test Completed --------------------")
        self._log(f"{'Test Duration':30}: {mins:02}:{secs:02} (mm:ss)")
        self._log(f"{'Final Delivered Throughput':30}: {total:.2f} Mbps")
        if stable_tp is not None:
            self._log(f"{'Stable Max Throughput':30}: {stable_tp:.2f} Mbps")
        self._log(f"{'Max Observed Throughput':30}: {max_tp:.2f} Mbps")
        self._log("---------------------------------------------------------------")

        self.ui["start_button"].config(state="normal")
        self.ui["stop_button"].config(state="disabled")
        self.ui["status_bar"].config(text="Test Stopped")

        for key in ["export_log_btn", "save_tp_graph_btn", "save_latency_graph_btn"]:
            if key in self.ui:
                self.ui[key].config(state="normal")

    def _log(self, msg):
        self.ui["output_area"].config(state='normal')
        self.ui["output_area"].insert("end", msg + "\n")
        self.ui["output_area"].config(state='disabled')
        self.ui["output_area"].see("end")