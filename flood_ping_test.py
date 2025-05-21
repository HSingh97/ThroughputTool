import subprocess
import threading
import time
import re
import psutil
import os
import signal
from collections import deque


# from cleanup_utils import kill_ping # Assuming not strictly needed

class FloodPingTest:
    """
    Measures maximum sustainable throughput between two devices by progressively
    adjusting (not restarting) flood ping instances until latency or packet loss
    exceeds defined limits, with a focus on stability and avoiding loss.
    """

    def __init__(self, remote_ip, iface, packet_size, profile, latency_limit,
                 loss_limit, ui_refs, data_store, graph,
                 update_metrics=None,  # Re-added to prevent TypeError from main.py
                 verbose_logging=False):
        """
        Initializes the FloodPingTest object.
        Args:
            update_metrics: Placeholder for compatibility with existing calls. Not used.
            verbose_logging (bool): If True, logs detailed internal steps.
                                   Defaults to False for concise user output.
        """
        self.remote_ip = remote_ip
        self.iface = iface
        self.packet_size = packet_size
        self.profile = profile
        self.latency_limit = latency_limit
        self.loss_limit = loss_limit
        self.ui = ui_refs
        self.data = data_store
        self.graph = graph
        # self.update_metrics = update_metrics # Still not used internally
        self.verbose_logging = verbose_logging

        self.stop_flag = False
        self.background_pids = []
        self.thread = threading.Thread(target=self._run_test, daemon=True)

        self.current_instance_count = 0
        self.fine_tuning = False
        self.consecutive_exceed_count = 0
        self.previous_loss = 0.0
        self.previous_latency = 0.0
        self.stable_count = 0
        self.max_stable_instances = 0
        self.running_pings = []
        self.probing = False
        self.max_stable_throughput = 0.0
        self.tx_at_max_throughput = 0.0
        self.rx_at_max_throughput = 0.0

        self.config = {
            "profile_initial_instances": {"Safe": 10, "Moderate": 20, "Aggressive": 30, "Default": 20},
            "min_instances_fallback": 1,
            "initial_reduction_factor": 20,
            "fine_tune_reduction_factor": 5,
            "probe_increment_factor": 5,
            "stability_threshold": 3,
            "consecutive_exceed_threshold": 3,
            "initial_settling_delay_s": 2,
            "metrics_ping_interval_s": "0.05",
            "metrics_ping_count": "20",
            "metrics_ping_command_timeout_s": 25,
            "metrics_ping_sample_duration_s": 1,
            "throughput_history_maxlen": 5,
            "plateau_percentage_threshold": 5,
            "main_loop_sleep_s": 1,
            "if_rate_measure_interval_s": 0.3,
            "process_kill_timeout_s": 2,
        }
        self.throughput_history = deque(maxlen=self.config["throughput_history_maxlen"])



    def run(self):
        self.thread.start()

    def stop(self):
        self.stop_flag = True
        self._kill_all_pings()

    def _kill_all_pings(self):
        if not self.running_pings:
            self.background_pids.clear()
            return

        if self.verbose_logging:
            self._log(f"[*] Killing {len(self.running_pings)} ping instances...")
        else:
            if len(self.running_pings) > 0:
                self._log(f"[*] Stopping active test and cleaning up resources...")

        for proc in self.running_pings:
            if proc.poll() is None:
                try:
                    process = psutil.Process(proc.pid)
                    process.terminate()
                    try:
                        process.wait(timeout=self.config["process_kill_timeout_s"])
                    except psutil.TimeoutExpired:
                        if self.verbose_logging:
                            self._log(f"[!] Process {proc.pid} did not terminate, killing...")
                        process.kill()
                        process.wait()
                except psutil.NoSuchProcess:
                    if self.verbose_logging:
                        self._log(f"[*] Process {proc.pid} already terminated.")
                except Exception as e:
                    if self.verbose_logging:
                        self._log(f"[!] Error killing process {proc.pid}: {e}")
        self.running_pings = []
        self.background_pids.clear()

    def _adjust_ping_instances(self, new_instance_count):
        new_instance_count = max(self.config.get("min_instances_fallback", 1), new_instance_count)

        if new_instance_count == self.current_instance_count:
            return

        if self.verbose_logging:
            self._log(f"[*] Adjusting ping instances from {self.current_instance_count} to {new_instance_count}")

        if new_instance_count > self.current_instance_count:
            instances_to_add = new_instance_count - self.current_instance_count
            for _ in range(instances_to_add):
                try:
                    proc = subprocess.Popen(["sudo", "ping", "-f", "-s", str(self.packet_size), self.remote_ip],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                            preexec_fn=os.setsid)
                    self.running_pings.append(proc)
                    self.background_pids.append(proc.pid)
                except Exception as e:
                    self._log(f"[!] Error starting ping process: {e}")  # Always log errors
        elif new_instance_count < self.current_instance_count:
            instances_to_remove = self.current_instance_count - new_instance_count
            for _ in range(instances_to_remove):
                if self.running_pings:
                    proc_to_kill = self.running_pings.pop(0)
                    if proc_to_kill.pid in self.background_pids:
                        self.background_pids.remove(proc_to_kill.pid)
                    if proc_to_kill.poll() is None:
                        try:
                            process = psutil.Process(proc_to_kill.pid)
                            process.terminate()
                            try:
                                process.wait(timeout=self.config["process_kill_timeout_s"] / 2)
                            except psutil.TimeoutExpired:
                                process.kill()
                                process.wait()
                        except psutil.NoSuchProcess:
                            pass
                        except Exception as e:
                            self._log(f"[!] Error killing process {proc_to_kill.pid}: {e}")  # Always log errors

        self.current_instance_count = len(self.running_pings)

    def _run_test(self):
        initial_instances = self.config["profile_initial_instances"].get(
            self.profile, self.config["profile_initial_instances"]["Default"]
        )
        ramp_up_increment = self.config["profile_initial_instances"].get(
            self.profile, self.config["profile_initial_instances"]["Default"]) // 2 or initial_instances

        self.current_instance_count = 0
        start_time = time.time()
        self.fine_tuning = False
        self.consecutive_exceed_count = 0
        self.previous_loss = 0.0
        self.previous_latency = 0.0
        self.stable_count = 0
        self.max_stable_instances = 0
        self.running_pings = []
        self.background_pids = []
        self.probing = False
        self.throughput_history.clear()
        self.max_stable_throughput = 0.0


        self._kill_all_pings()
        self._adjust_ping_instances(initial_instances)
        if not self.running_pings and initial_instances > 0:
            self._abort(
                f"Failed to start initial {initial_instances} ping instances. Check sudo permissions or ping command.",
                start_time)
            return

        self._log(
            f"\n=========================== Test Started at {time.strftime('%H:%M:%S')} ===========================")
        self._log(f"[*] Initial instances: {self.current_instance_count}, Profile: {self.profile}")
        self._log(f"[*] Latency Limit: {self.latency_limit:.1f} ms, Loss Limit: {self.loss_limit:.1f}%")

        time.sleep(self.config["initial_settling_delay_s"])
        first_throughput_cycle = True

        while not self.stop_flag:
            # Metrics Ping
            metrics_ping_cmd = [
                "ping", "-i", self.config["metrics_ping_interval_s"],
                "-s", str(self.packet_size), "-c", self.config["metrics_ping_count"],
                self.remote_ip
            ]
            ping_proc = subprocess.Popen(metrics_ping_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

            # Initialize with worst-case defaults for the current cycle's metrics
            icmp_loss_this_cycle = 100.0
            icmp_latency_this_cycle = 9999.0

            try:
                ping_output, _ = ping_proc.communicate(timeout=self.config["metrics_ping_command_timeout_s"])
                loss_match = re.search(r"(\d+(?:\.\d+)?)% packet loss", ping_output)
                rtt_match = re.search(r"rtt min/avg/max/mdev = [\d.]+/([\d.]+)/", ping_output)
                if loss_match: icmp_loss_this_cycle = float(loss_match.group(1))
                if rtt_match: icmp_latency_this_cycle = float(rtt_match.group(1))

                # Handle edge cases from ping output
                if not loss_match and not rtt_match and "100% packet loss" in ping_output:
                    icmp_loss_this_cycle = 100.0
                elif "0% packet loss" in ping_output and not rtt_match and not loss_match:  # Ensure loss_match is also checked
                    # If 0% loss reported but no RTT, might indicate other issues, keep high latency
                    icmp_latency_this_cycle = 9999.0
                    icmp_loss_this_cycle = 0.0  # Explicitly set loss if "0% packet loss" is present

            except subprocess.TimeoutExpired:
                if self.verbose_logging:
                    self._log("[!] Metrics ping command timed out. Assuming high latency/loss.")
                ping_proc.kill()
                # icmp_loss_this_cycle and icmp_latency_this_cycle remain at worst-case defaults
            except Exception as e:
                self._log(f"[!] Error during metrics ping: {e}")
                # icmp_loss_this_cycle and icmp_latency_this_cycle remain at worst-case defaults

            current_tx = self._get_if_rate('tx')
            current_rx = self._get_if_rate('rx')
            total_throughput = current_tx + current_rx

            current_time = time.time()
            self.data["tx"].append(current_tx)
            self.data["rx"].append(current_rx)
            self.data["latency"].append(icmp_latency_this_cycle)  # Use metrics from this cycle
            self.data["throughput"].append(total_throughput)
            self.data["timestamp"].append(current_time)

            if self.graph:
                self.graph.update_graphs(self.data["timestamp"], self.data["tx"], self.data["rx"], self.data["latency"])

            log_msg_status = (
                f"[+] Instances: {self.current_instance_count:<3} | Tx: {current_tx:7.2f} Mbps | Rx: {current_rx:7.2f} Mbps | "
                f"Latency: {icmp_latency_this_cycle:6.2f} ms | Loss: {icmp_loss_this_cycle:5.1f}%"
            # Use metrics from this cycle
            )
            if self.fine_tuning: log_msg_status += " (Fine-tuning)"
            if self.probing: log_msg_status += " (Probing)"
            self._log(log_msg_status)

            # Decision logic based on this cycle's metrics
            if icmp_latency_this_cycle > self.latency_limit or icmp_loss_this_cycle > self.loss_limit:
                self.consecutive_exceed_count += 1
                self.stable_count = 0
                self.probing = False

                if not self.fine_tuning:
                    self._log(
                        f"[!] Limits exceeded (L:{icmp_latency_this_cycle:.2f}ms, P:{icmp_loss_this_cycle:.1f}%). Entering fine-tuning. Reducing instances.")
                    self.fine_tuning = True
                    reduction = self.config["initial_reduction_factor"]
                    self._adjust_ping_instances(self.current_instance_count - reduction)
                else:
                    if self.consecutive_exceed_count >= self.config["consecutive_exceed_threshold"]:
                        self._abort(
                            f"Failed to stabilize after {self.config['consecutive_exceed_threshold']} attempts during fine-tuning. "
                            f"Max Stable: {self.max_stable_instances} inst. ({self.max_stable_throughput:.2f} Mbps)",
                            start_time
                        )
                        return
                    if self.verbose_logging:
                        self._log(f"[!] Limits still exceeded during fine-tuning. Reducing instances further.")
                    reduction = self.config["fine_tune_reduction_factor"]
                    self._adjust_ping_instances(self.current_instance_count - reduction)

                self.previous_loss = icmp_loss_this_cycle
                self.previous_latency = icmp_latency_this_cycle

                # Check if already at minimum instances and still failing
                if self.current_instance_count <= self.config.get("min_instances_fallback", 1) and \
                        (icmp_latency_this_cycle > self.latency_limit or icmp_loss_this_cycle > self.loss_limit):
                    self._abort(f"Reduced to minimum instances ({self.current_instance_count}) but still unstable.",
                                start_time)
                    return
            else:  # Within limits
                self.consecutive_exceed_count = 0  # Reset exceed count
                if total_throughput > self.max_stable_throughput:
                    self.max_stable_throughput = total_throughput
                    self.max_stable_instances = self.current_instance_count
                    self.tx_at_max_throughput = current_tx
                    self.rx_at_max_throughput = current_rx
                    #self._log(
                    #    f"[*] New max stable throughput: {self.max_stable_throughput:.2f} Mbps at {self.max_stable_instances} instances.")

                if self.fine_tuning:
                    self.stable_count += 1
                    if self.stable_count >= self.config["stability_threshold"]:
                        if not self.probing:  # Just stabilized after exceeding limits
                            self._log(
                                f"[+] Stabilized at {self.current_instance_count} instances ({total_throughput:.2f} Mbps) after fine-tuning. "
                                f"Now probing for higher throughput..."
                            )
                            self.probing = True
                            self._adjust_ping_instances(
                                self.current_instance_count + self.config["probe_increment_factor"])
                            self.stable_count = 0  # Reset stable count for the new probed level
                        else:  # Was already probing and this new probed level is stable
                            self._log(
                                f"[+] Probed level ({self.current_instance_count} inst, {total_throughput:.2f} Mbps) is stable. "
                                f"This is the current max sustainable."
                            )
                            self._abort(
                                f"Test completed. Max Stable: {self.max_stable_instances} inst. ({self.max_stable_throughput:.2f} Mbps)",
                                start_time)
                            return
                    else:  # Still in fine-tuning, accumulating stable counts
                        if self.verbose_logging:
                            self._log(
                                f"[*] Fine-tuning: Stable count {self.stable_count}/{self.config['stability_threshold']} at {self.current_instance_count} instances.")
                        # No change in instances, wait for more stable readings

                else:  # Not fine-tuning (initial ramp-up or probing that succeeded and is now stable)
                    if self.probing:
                        # This means we were probing, increased instances, and this new level is stable.
                        # The current logic will make it enter the self.fine_tuning=True, self.probing=True, stable_count check above
                        # on subsequent stable readings.
                        # We can let it continue to gather stable_count at this probed level.
                        if self.verbose_logging:
                            self._log(
                                f"[*] Probed level ({self.current_instance_count} inst.) is stable. Monitoring for stability threshold.")
                        # It will go through the fine_tuning=True & probing=True path above once stable_count threshold is met.
                    else:  # Initial ramp-up phase
                        if self.verbose_logging:
                            self._log(f"[*] Ramp-up: Increasing instances by {ramp_up_increment}.")
                        self._adjust_ping_instances(self.current_instance_count + ramp_up_increment)

                self.previous_loss = icmp_loss_this_cycle
                self.previous_latency = icmp_latency_this_cycle

            # Plateau Detection
            self.throughput_history.append(total_throughput)
            if not first_throughput_cycle and len(self.throughput_history) >= self.config["throughput_history_maxlen"]:
                # Check for plateau only if not fine-tuning aggressively (or recently stabilized)
                if not self.fine_tuning or self.stable_count > 1:  # Allow plateau check if stable or in general ramp up
                    avg_throughput_history = sum(self.throughput_history) / len(self.throughput_history) if len(
                        self.throughput_history) > 0 else 0
                    if avg_throughput_history > 0:  # Avoid division by zero
                        delta_throughput = max(self.throughput_history) - min(self.throughput_history)
                        percentage_change = (delta_throughput / avg_throughput_history) * 100
                        if percentage_change < self.config["plateau_percentage_threshold"]:
                            if self.verbose_logging:
                                self._log(
                                    f"[*] Throughput history: {['{:.2f}'.format(x) for x in self.throughput_history]}, "
                                    f"Avg: {avg_throughput_history:.2f}, Delta: {delta_throughput:.2f}, Change: {percentage_change:.2f}%"
                                )
                            self._abort(
                                f"Throughput plateau detected. Max stable: {self.max_stable_throughput:.2f} Mbps.",
                                start_time)
                            return

            if first_throughput_cycle:
                first_throughput_cycle = False

            # === START: Modified Sleep Logic ===
            if self.stop_flag: break  # Check stop_flag again before sleeping

            current_loop_sleep = self.config["main_loop_sleep_s"]
            # Condition for extended sleep:
            if self.fine_tuning and \
                    (icmp_latency_this_cycle > self.latency_limit or icmp_loss_this_cycle > self.loss_limit) and \
                    self.consecutive_exceed_count > 0:
                current_loop_sleep += 1.0  # Add an extra second (configurable if needed)
                if self.verbose_logging:
                    self._log(f"[*] Extended settling time to {current_loop_sleep:.1f}s due to fine-tuning stress.")

            time.sleep(current_loop_sleep)
            # === END: Modified Sleep Logic ===

        # End of while loop
        self._final_summary(start_time)

    def _abort(self, reason, start_time):
        self._log(f"\n[!] ABORTING: {reason}")  # Always log abort reason
        if not self.stop_flag:
            self.stop_flag = True
            self._kill_all_pings()
        self._final_summary(start_time)

    def _final_summary(self, start_time):
        duration = int(time.time() - start_time)
        mins, secs = divmod(duration, 60)

        # --- Configuration for the box ---
        box_width = 62
        content_width = box_width - 4
        key_text_max_width = 25
        separator = " : "

        summary_lines = []

        def add_line_to_summary(key_text, value_text=""):
            # (Previous add_line_to_summary helper function code here - no changes needed in it)
            if not key_text and not value_text:
                summary_lines.append(f"| {' ' * content_width} |")
            elif not value_text:
                display_text = key_text[:content_width]
                padding_total = content_width - len(display_text)
                padding_left = padding_total // 2
                padding_right = padding_total - padding_left
                summary_lines.append(f"| {' ' * padding_left}{display_text}{' ' * padding_right} |")
            else:
                formatted_key = key_text[:key_text_max_width].ljust(key_text_max_width)
                value_part_width = content_width - len(formatted_key) - len(separator)
                if value_part_width < 0: value_part_width = 0
                formatted_value = value_text[:value_part_width].ljust(value_part_width)
                line_content = f"{formatted_key}{separator}{formatted_value}"
                summary_lines.append(f"| {line_content} |")

        title = "Flood Ping Test Summary"
        border_line = f"+{'-' * (box_width - 2)}+"

        summary_lines.append("\n" + border_line)
        add_line_to_summary(title)
        summary_lines.append(border_line)
        add_line_to_summary("Test Duration", f"{mins:02}:{secs:02} (mm:ss)")
        add_line_to_summary("Max Stable Instances", str(self.max_stable_instances))
        add_line_to_summary("", "")
        add_line_to_summary("Max Stable Throughput", f"{self.max_stable_throughput:.2f} Mbps (Total)")
        add_line_to_summary("  Tx (at Max Stable)", f"{self.tx_at_max_throughput:.2f} Mbps")
        add_line_to_summary("  Rx (at Max Stable)", f"{self.rx_at_max_throughput:.2f} Mbps")

        if self.verbose_logging:
            last_tx_live = self.data['tx'][-1] if self.data['tx'] else 0.0
            last_rx_live = self.data['rx'][-1] if self.data['rx'] else 0.0
            total_live = last_tx_live + last_rx_live
            add_line_to_summary("", "")
            add_line_to_summary("--- Last Measured Live Data (Verbose) ---")
            add_line_to_summary("  Tx", f"{last_tx_live:.2f} Mbps")
            add_line_to_summary("  Rx", f"{last_rx_live:.2f} Mbps")
            add_line_to_summary("  Total", f"{total_live:.2f} Mbps")

        summary_lines.append(border_line)

        # --- Log the summary box with DEBUG PRINT for length ---
        #print("\n--- DEBUG: Final Summary String Lengths ---")  # For your console
        for line in summary_lines:
            # This print goes to the console where you launched the app
            #print(f"Length: {len(line):<4} Line: '{line}'")
            self._log(line)  # This goes to your UI
        #print("--- END DEBUG ---")

        # --- UI updates ---
        if self.ui:
            # (UI update code as before)
            try:
                self.ui['start_button'].config(state="normal")
                self.ui['stop_button'].config(state="disabled")
                self.ui['status_bar'].config(text="Test Stopped/Completed")
                if 'export_log_btn' in self.ui: self.ui['export_log_btn'].config(state="normal")
                if 'save_tp_graph_btn' in self.ui: self.ui['save_tp_graph_btn'].config(state="normal")
                if 'save_latency_graph_btn' in self.ui: self.ui['save_latency_graph_btn'].config(state="normal")
            except Exception as e:
                self._log(f"[UI Error] Failed to update UI elements state: {e}")

    def _log(self, msg):
        # print(msg) # For non-UI debugging if needed
        if self.ui and self.ui.get('output_area'):
            try:
                self.ui['output_area'].config(state='normal')
                self.ui['output_area'].insert("end", msg + "\n")
                self.ui['output_area'].config(state='disabled')
                self.ui['output_area'].see("end")
            except Exception as e:
                print(f"UI Logging Error: {e}")  # Fallback
        else:  # If no UI, print to console
            print(msg)

    def _get_if_rate(self, direction):
        try:
            stats1 = psutil.net_io_counters(pernic=True).get(self.iface)
            if not stats1:
                if self.verbose_logging:
                    self._log(f"[Warning] Interface {self.iface} not found in stats1 for _get_if_rate.")
                return 0.0
            time.sleep(self.config["if_rate_measure_interval_s"])
            stats2 = psutil.net_io_counters(pernic=True).get(self.iface)
            if not stats2:
                if self.verbose_logging:
                    self._log(f"[Warning] Interface {self.iface} not found in stats2 for _get_if_rate.")
                return 0.0

            interval = self.config["if_rate_measure_interval_s"]
            delta_bytes = (stats2.bytes_sent - stats1.bytes_sent) if direction == 'tx' else (
                        stats2.bytes_recv - stats1.bytes_recv)
            rate = (delta_bytes * 8) / (1_000_000 * interval)
            return round(rate, 2) if rate >= 0 else 0.0  # Ensure non-negative, though delta should be >=0
        except Exception as e:
            if self.verbose_logging:
                self._log(f"[Error] Getting interface rate for {self.iface} ({direction}): {e}")
            return 0.0