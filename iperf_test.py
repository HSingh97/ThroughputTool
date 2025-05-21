import subprocess
import threading
import json
import time
# import psutil # Not directly used in this class, but kill_iperf might use it
import re
# import os # Not directly used in this class
from collections import deque

from cleanup_utils import kill_iperf


class IperfTest:
    """
    Conducts iPerf3 tests, adjusting parameters (bandwidth for UDP, streams for TCP)
    to find maximum throughput, while monitoring latency and packet loss (for UDP).
    Ensures original iPerf3 testing logic is preserved for accuracy.
    """

    def __init__(self, remote_ip, iface, protocol, packet_size, direction, profile,
                 ui_refs, data_store, graph, update_metrics=None, verbose_logging=False):
        self.remote_ip = remote_ip
        self.iface = iface  # Note: 'iface' is not used in iperf3 cmd in this version
        self.protocol = protocol.upper()
        self.direction = direction
        self.packet_size = packet_size
        self.profile = profile
        self.ui = ui_refs
        self.data = data_store
        self.graph = graph
        self.update_metrics = update_metrics
        self.verbose_logging = verbose_logging

        bootstrap_log_msg = f"[*] IperfTest instance created. Verbose logging is: {'ON' if self.verbose_logging else 'OFF'}"
        try:
            if self.ui and self.ui.get('output_area'):
                self._log(bootstrap_log_msg)
            else:
                print(bootstrap_log_msg)
        except Exception:
            print(bootstrap_log_msg)

        self.stop_flag = False
        self.latency_stop_flag = False
        self.latency_thread = None
        self.start_time = 0
        self.concluded_event = threading.Event()

        try:
            self.loss_limit = float(self.ui['entries']['Loss Threshold (%)'].get())
        except (ValueError, TypeError, AttributeError):
            print(f"IperfTest (init): [Warning] Invalid or missing Loss Threshold. Defaulting to 1.0%.")
            self.loss_limit = 1.0

        self.thread = threading.Thread(target=self._run_test, daemon=True)

        self.config = {
            "iperf_test_duration_s": 5,
            "iperf_command_timeout_s": 25,
            "latency_ping_interval_s": 1,
            "latency_ping_packet_timeout_s": 1,
            "latency_ping_count": 1,
            "udp_initial_bw_mbps": 10,
            "udp_bw_step_mbps": {"Safe": 10, "Moderate": 30, "Aggressive": 50, "Default": 30},
            "udp_initial_streams": 2,
            "udp_streams_increment_after_bw_freeze": 1,
            "udp_max_loss_retry_attempts": 1,
            "udp_no_gain_threshold_after_bw_freeze": 3,
            "tcp_initial_streams": 2,
            "tcp_streams_increment": 1,
            "tcp_no_gain_threshold": 5,
            "tcp_plateau_iterations": 3,
            "tcp_plateau_percentage_variance": 3.0,
            "main_loop_settle_s": 0.5,
            "process_kill_timeout_s": 2,
        }

        self.tcp_throughput_history = deque(maxlen=self.config["tcp_plateau_iterations"])

        self.max_achieved_tp_overall = 0.0
        self.max_achieved_tp_tx = 0.0
        self.max_achieved_tp_rx = 0.0
        self.max_achieved_tp_udp_lossless_bw = 0
        self.max_achieved_tp_streams = 0

    def run(self):
        if self.ui.get('export_log_btn'): self.ui['export_log_btn'].config(state='disabled')
        if self.ui.get('save_tp_graph_btn'): self.ui['save_tp_graph_btn'].config(state='disabled')
        if self.ui.get('save_latency_graph_btn'): self.ui['save_latency_graph_btn'].config(state='disabled')
        self.thread.start()

    def stop(self, reason="User initiated"):
        if self.concluded_event.is_set():
            if self.verbose_logging: self._log("[*] Stop called but test already concluded.")
            return
        if self.stop_flag:
            if self.verbose_logging: self._log("[*] Stop called again while already stopping.")
            kill_iperf()
            return
        self._log(f"[*] Test stopping: {reason}")
        self.stop_flag = True
        self.latency_stop_flag = True
        if self.verbose_logging:
            self._log("[*] Attempting to kill iPerf processes due to user stop request...")
        kill_iperf()

    def _run_test(self):
        self.start_time = time.time()
        self.concluded_event.clear()
        self.tcp_throughput_history.clear()

        self._log(
            f"\n======================= iPerf3 {self.protocol} Test ({self.direction}) Started at {time.strftime('%H:%M:%S')} =======================")
        if self.verbose_logging:
            self._log(
                f"[*] Profile: {self.profile}, Target IP: {self.remote_ip}, Packet Size: {self.packet_size if self.packet_size else 'Default'}")
        if self.protocol == "UDP":
            self._log(f"[*] UDP Loss Threshold: {self.loss_limit:.2f}%")

        self._start_latency_monitor()

        current_streams = 0
        current_udp_bw_mbps = 0

        if self.protocol == "UDP":
            current_streams = self.config["udp_initial_streams"]
            current_udp_bw_mbps = self.config["udp_initial_bw_mbps"]
            udp_bw_step = self.config["udp_bw_step_mbps"].get(self.profile, self.config["udp_bw_step_mbps"]["Default"])
            udp_bw_frozen = False
            udp_last_lossless_target_bw = current_udp_bw_mbps
            udp_loss_retry_count = 0
            udp_no_gain_stream_phase_count = 0
        else:  # TCP
            current_streams = self.config["tcp_initial_streams"]
            tcp_no_gain_count = 0

        iteration = 0
        while not self.stop_flag:
            iteration += 1
            current_latency = self.data["latency"][-1] if self.data["latency"] else 0.0

            current_tx_mbps, current_rx_mbps, current_total_achieved_mbps = 0.0, 0.0, 0.0
            current_loss_percent = 100.0 if self.protocol == "UDP" else 0.0
            log_msg_detail = ""
            used_server_perspective_for_tx = False  # Default, set true if applicable
            is_interrupt_error = False

            cmd = ["iperf3", "-c", self.remote_ip,
                   "-t", str(self.config["iperf_test_duration_s"]),
                   "-J",
                   "-P", str(current_streams)]

            if self.protocol == "TCP":
                cmd.append("--get-server-output")

            if self.packet_size:
                cmd.extend(["-l", str(self.packet_size)])
            if self.protocol == "UDP":
                cmd.extend(["-u", "-b", f"{current_udp_bw_mbps}M"])
            if self.direction == "Downlink":
                cmd.append("-R")
            elif self.direction == "Bi-Di":
                cmd.append("--bidir")

            if self.verbose_logging:
                self._log(f"[*] Iter {iteration}: Executing: {' '.join(cmd)}")

            iperf_output_str = ""
            proc_returncode = -1
            result_json = None

            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, timeout=self.config["iperf_command_timeout_s"], check=False)
                iperf_output_str = proc.stdout
                proc_returncode = proc.returncode

                if proc_returncode != 0 and self.verbose_logging:
                    self._log(
                        f"[Warning] iPerf3 process (iter {iteration}) exited with code {proc_returncode}. Output: {iperf_output_str[:200]}")

                if iperf_output_str:
                    json_start_index = iperf_output_str.find('{')
                    if json_start_index != -1:
                        json_data_str = iperf_output_str[json_start_index:]
                        result_json = json.loads(json_data_str)
                        if result_json and self.protocol == "TCP" and self.verbose_logging:
                            self._log(
                                f"[*] Iter {iteration} RAW JSON (End section): {json.dumps(result_json.get('end', {}), indent=2)}")
                    elif self.verbose_logging:
                        self._log(
                            f"[Warning] No JSON object start found in iPerf3 output (iter {iteration}). Output: {iperf_output_str[:200]}")
                elif self.verbose_logging:
                    self._log(f"[Warning] No output received from iPerf3 (iter {iteration}). RC: {proc_returncode}")

            except subprocess.TimeoutExpired:
                self._log(f"[!] iPerf3 command (iter {iteration}) timed out.")
                self._conclude_test_and_summarize(f"iPerf3 timeout (iter {iteration})")
                return
            except FileNotFoundError:
                self._log("[!] iPerf3 command not found.")
                self._conclude_test_and_summarize("iPerf3 not found")
                return
            except json.JSONDecodeError as e:
                self._log(f"[!] Failed to parse iPerf3 JSON (iter {iteration}): {e}. Output: {iperf_output_str[:500]}")
            except Exception as e:
                self._log(f"[!] Error during iPerf3 execution/parsing (iter {iteration}): {e}")

            if result_json:
                iperf_error_from_json = result_json.get("error")
                if iperf_error_from_json:
                    is_user_interrupt_type_error = (
                            "interrupt" in iperf_error_from_json.lower() or
                            "terminated" in iperf_error_from_json.lower() or
                            "client has exited" in iperf_error_from_json.lower()
                    )
                    if is_user_interrupt_type_error:
                        is_interrupt_error = True
                        if self.verbose_logging:
                            self._log(
                                f"[*] iPerf3 test interrupted by client/user (iter {iteration}): {iperf_error_from_json}")
                    else:
                        self._log(f"[!] iPerf3 reported error in JSON (iter {iteration}): {iperf_error_from_json}")

                    if not is_user_interrupt_type_error:
                        if "parameter conflict" in iperf_error_from_json.lower() or \
                                "reverse mode conflict" in iperf_error_from_json.lower() or \
                                ("unable to connect" in iperf_error_from_json.lower() and proc_returncode != 0) or \
                                ("connection refused" in iperf_error_from_json.lower() and proc_returncode != 0):
                            self._conclude_test_and_summarize(
                                f"iPerf3 critical error: {iperf_error_from_json} (iter {iteration})")
                            return
            elif "connection refused" in iperf_output_str.lower() or "unable to connect" in iperf_output_str.lower():
                self._log(f"[!] iPerf3 connection failed (iter {iteration}): {iperf_output_str[:200]}")
                self._conclude_test_and_summarize(f"iPerf3 connection failed (iter {iteration})")
                return

            if result_json:
                if self.protocol == "UDP":
                    sum_data = result_json.get("end", {}).get("sum", {})
                    if sum_data:
                        achieved_bps = sum_data.get("bits_per_second", 0)
                        current_total_achieved_mbps = achieved_bps / 1_000_000.0
                        current_loss_percent = sum_data.get("lost_percent", 0.0)
                        if achieved_bps == 0 and sum_data.get("bytes", 0) == 0 and current_udp_bw_mbps > 0:
                            current_loss_percent = 100.0
                    else:
                        if self.verbose_logging: self._log(
                            f"[Warning] UDP Iter {iteration}: No 'sum' data in JSON, using default 100% loss.")

                    if self.direction == "Downlink":
                        current_rx_mbps = current_total_achieved_mbps
                        current_tx_mbps = 0.0
                    elif self.direction == "Uplink":
                        current_tx_mbps = current_total_achieved_mbps
                        current_rx_mbps = 0.0
                    elif self.direction == "Bi-Di":
                        current_tx_mbps = current_total_achieved_mbps
                        current_rx_mbps = 0.0
                        if self.verbose_logging:
                            self._log(
                                f"[*] UDP Bi-Di Iter {iteration}: 'sum' data attributed to Tx. Rx considered 0 from this summary.")

                else:  # TCP
                    end_data = result_json.get("end", {})
                    sum_sent_data_last = end_data.get("sum_sent", {})  # May be the last of duplicates
                    sum_received_data_last = end_data.get("sum_received", {})  # May be the last of duplicates

                    uplink_bps_to_use, downlink_bps_to_use = 0, 0
                    log_source_tx, log_source_rx = "(NoData)", "(NoData)"

                    if self.direction == "Uplink":
                        # For Uplink with --get-server-output, server's report is in client's "sum_received"
                        if sum_received_data_last and "bits_per_second" in sum_received_data_last:
                            uplink_bps_to_use = sum_received_data_last.get("bits_per_second", 0)
                            log_source_tx, used_server_perspective_for_tx = "(SrvRcv via CliSumRcv)", True
                        elif sum_sent_data_last and "bits_per_second" in sum_sent_data_last:  # Fallback
                            uplink_bps_to_use = sum_sent_data_last.get("bits_per_second", 0)
                            log_source_tx = "(CliSent - fallback)"
                            if self.verbose_logging: self._log(
                                f"[Warning] TCP Uplink Iter {iteration}: Server's received rate unavailable, using client's sent rate.")
                        else:
                            if self.verbose_logging: self._log(
                                f"[Warning] TCP Uplink Iter {iteration}: No usable uplink data found.")
                        current_tx_mbps = uplink_bps_to_use / 1_000_000.0
                        current_rx_mbps = 0.0

                    elif self.direction == "Downlink":
                        # Client is receiver, sum_received_data is authoritative.
                        if sum_received_data_last and "bits_per_second" in sum_received_data_last:
                            downlink_bps_to_use = sum_received_data_last.get("bits_per_second", 0)
                            log_source_rx = "(CliRcv)"
                        else:
                            if self.verbose_logging: self._log(
                                f"[Warning] TCP Downlink Iter {iteration}: No downlink data (sum_received) found.")
                        current_tx_mbps = 0.0
                        current_rx_mbps = downlink_bps_to_use / 1_000_000.0
                        log_source_tx = "(N/A)"

                    elif self.direction == "Bi-Di":
                        # FIX FOR TCP Bi-Di: Sum from end.streams due to potential duplicate keys in end.sum_*
                        # and to accurately get receiver perspective for each direction.
                        calculated_uplink_bps = 0
                        calculated_downlink_bps = 0
                        end_streams_data = end_data.get("streams", [])
                        num_tx_streams_processed = 0
                        num_rx_streams_processed = 0

                        if not end_streams_data:
                            if self.verbose_logging:
                                self._log(
                                    f"[Warning] TCP BiDi Iter {iteration}: 'end.streams' array is missing. Using potentially ambiguous sum objects.")
                            # Fallback to original (potentially flawed due to duplicate keys) logic if streams array is empty
                            # This path indicates an unexpected JSON structure from iPerf3.
                            # Client's TX rate (measured by server) appears in client's "sum_received" (ideally the one with sender=true)
                            # Client's RX rate (data sent by server) appears in client's "sum_sent" (ideally the one with sender=false, or sum_received with sender=false)
                            # The original script logic picked the LAST sum_received for TX and LAST sum_sent for RX.
                            # We will replicate that as a fallback, though it might not be what user expects if duplicates are handled differently.
                            # To better match user expectation if they wanted specific "sender:true/false" sums:
                            # This fallback is tricky without iterating raw JSON string to find specific sum blocks.
                            # Sticking to last sum_obj as before for this rare fallback.
                            if sum_received_data_last and "bits_per_second" in sum_received_data_last:
                                calculated_uplink_bps = sum_received_data_last.get("bits_per_second", 0)
                                log_source_tx = "(Fallback SrvRcv via LAST CliSumRcv)"  # Potentially not user's target TX
                            if sum_sent_data_last and "bits_per_second" in sum_sent_data_last:
                                calculated_downlink_bps = sum_sent_data_last.get("bits_per_second", 0)
                                log_source_rx = "(Fallback CliRcv via LAST CliSumSent)"  # Potentially not user's target RX

                        else:  # Primary logic: Process end.streams
                            for stream_report in end_streams_data:
                                stream_sender_info = stream_report.get("sender", {})
                                stream_receiver_info = stream_report.get("receiver", {})

                                # 'is_client_tx_stream' means this stream was initiated by the client to send data
                                is_client_tx_stream = stream_sender_info.get("sender", False)

                                if is_client_tx_stream:
                                    # Client -> Server flow. We want what the server received for this stream.
                                    # This is in stream_receiver_info.bits_per_second for this stream group.
                                    calculated_uplink_bps += stream_receiver_info.get("bits_per_second", 0)
                                    num_tx_streams_processed += 1
                                else:
                                    # Server -> Client flow (client is receiver for this stream_sender_info).
                                    # We want what the client received for this stream.
                                    # This is in stream_receiver_info.bits_per_second.
                                    calculated_downlink_bps += stream_receiver_info.get("bits_per_second", 0)
                                    num_rx_streams_processed += 1

                            log_source_tx = f"(Summed {num_tx_streams_processed} SrvRcv from end.streams)"
                            log_source_rx = f"(Summed {num_rx_streams_processed} CliRcv from end.streams)"
                            used_server_perspective_for_tx = True  # As we sum server's measured reception

                        current_tx_mbps = calculated_uplink_bps / 1_000_000.0
                        current_rx_mbps = calculated_downlink_bps / 1_000_000.0

                    current_total_achieved_mbps = current_tx_mbps + current_rx_mbps

                    if self.verbose_logging:
                        details = []
                        if self.direction != "Downlink" and log_source_tx not in ["(NoData)", "(N/A)"]:
                            details.append(f"TxSrc:{log_source_tx}")
                        if self.direction != "Uplink" and log_source_rx not in ["(NoData)", "(N/A)"]:
                            details.append(f"RxSrc:{log_source_rx}")
                        if details:
                            log_msg_detail = " (" + ", ".join(details) + ")"

            if self.protocol == "UDP":
                log_msg = (f"Target UDP BW: {current_udp_bw_mbps:<4}M | Streams: {current_streams:<2} | "
                           f"Achieved: {current_total_achieved_mbps:7.2f} Mbps | Loss: {current_loss_percent:5.2f}% | Latency: {current_latency:6.2f} ms")
            else:  # TCP
                log_msg = (
                    f"TCP Streams: {current_streams:<2} | Tx: {current_tx_mbps:7.2f} Mbps | Rx: {current_rx_mbps:7.2f} Mbps | "
                    f"Total: {current_total_achieved_mbps:7.2f} Mbps{log_msg_detail} | Latency: {current_latency:6.2f} ms")
            self._log(log_msg)

            timestamp = time.time()
            self.data["tx"].append(current_tx_mbps)
            self.data["rx"].append(current_rx_mbps)
            self.data["throughput"].append(current_total_achieved_mbps)
            self.data["timestamp"].append(timestamp)

            if self.update_metrics:
                self.update_metrics(tx=current_tx_mbps, rx=current_rx_mbps, latency=current_latency,
                                    loss=current_loss_percent, duration_secs=int(timestamp - self.start_time),
                                    total=current_total_achieved_mbps)
            if self.graph:
                self.graph.update_graphs(self.data["timestamp"], self.data["tx"], self.data["rx"], self.data["latency"])

            # --- Decision Logic (parameter adjustment, max throughput update) ---
            if self.protocol == "UDP":
                if current_loss_percent <= self.loss_limit:
                    udp_loss_retry_count = 0
                    if not is_interrupt_error and current_total_achieved_mbps > self.max_achieved_tp_overall:
                        self.max_achieved_tp_overall, self.max_achieved_tp_tx, self.max_achieved_tp_rx = current_total_achieved_mbps, current_tx_mbps, current_rx_mbps
                        self.max_achieved_tp_udp_lossless_bw, self.max_achieved_tp_streams = current_udp_bw_mbps, current_streams
                        udp_no_gain_stream_phase_count = 0
                        if self.verbose_logging: self._log(
                            f"[*] New max UDP throughput: {self.max_achieved_tp_overall:.2f} Mbps at {current_udp_bw_mbps}M target, {current_streams} streams")
                    elif is_interrupt_error and self.verbose_logging:
                        self._log(
                            f"[*] Iter {iteration} (UDP): Test interrupted, throughput ({current_total_achieved_mbps:.2f} Mbps) not for max.")
                    if not udp_bw_frozen:
                        udp_last_lossless_target_bw = current_udp_bw_mbps
                        current_udp_bw_mbps += udp_bw_step
                    else:
                        if current_total_achieved_mbps <= self.max_achieved_tp_overall * 0.99 and self.max_achieved_tp_overall > 0:  # If results dip after freezing BW
                            udp_no_gain_stream_phase_count += 1
                        else:
                            udp_no_gain_stream_phase_count = 0  # Reset if we see gain or stability
                        current_streams += self.config["udp_streams_increment_after_bw_freeze"]
                else:  # Loss > limit
                    udp_loss_retry_count += 1
                    if self.verbose_logging: self._log(
                        f"[!] UDP Iter {iteration}: Loss {current_loss_percent:.2f}% > {self.loss_limit:.2f}%. Retry {udp_loss_retry_count}/{self.config['udp_max_loss_retry_attempts']}.")
                    if udp_loss_retry_count > self.config["udp_max_loss_retry_attempts"]:
                        if not udp_bw_frozen:
                            self._log(
                                f"[*] UDP Iter {iteration}: Freezing UDP bandwidth at last good/attempted: {udp_last_lossless_target_bw}M or lower. Will inc streams.")
                            udp_bw_frozen = True
                            current_udp_bw_mbps = max(1,
                                                      udp_last_lossless_target_bw)  # Revert to last known good target BW
                            # Consider if stepping back one step is better: max(1, udp_last_lossless_target_bw - udp_bw_step)
                        else:  # Already frozen, still getting loss after retries
                            self._conclude_test_and_summarize(
                                f"UDP loss {current_loss_percent:.2f}% > {self.loss_limit:.2f}% (iter {iteration}, post-freeze, after stream increments)")
                            return
                        udp_loss_retry_count = 0
                if udp_bw_frozen and udp_no_gain_stream_phase_count >= self.config[
                    "udp_no_gain_threshold_after_bw_freeze"]:
                    self._conclude_test_and_summarize(
                        f"UDP: No gain after {self.config['udp_no_gain_threshold_after_bw_freeze']} stream increments (iter {iteration}).")
                    return
                if current_udp_bw_mbps > 20000 or current_streams > 64:
                    self._conclude_test_and_summarize(f"UDP params exceeded safety limits (iter {iteration}).")
                    return
            else:  # TCP
                is_valid_data_for_calc = (current_total_achieved_mbps > 0.001)

                if not is_interrupt_error and is_valid_data_for_calc:
                    if current_total_achieved_mbps > self.max_achieved_tp_overall:
                        self.max_achieved_tp_overall, self.max_achieved_tp_tx, self.max_achieved_tp_rx = current_total_achieved_mbps, current_tx_mbps, current_rx_mbps
                        self.max_achieved_tp_streams = current_streams
                        tcp_no_gain_count = 0
                        self.tcp_throughput_history.clear()
                        if self.verbose_logging:
                            self._log(
                                f"[*] New max TCP throughput: {self.max_achieved_tp_overall:.2f} Mbps at {current_streams} streams{log_msg_detail} (Iter {iteration})")  # Use populated log_msg_detail
                    else:
                        if current_streams > self.config["tcp_initial_streams"] or self.max_achieved_tp_overall > 1:
                            tcp_no_gain_count += 1
                        if self.verbose_logging: self._log(
                            f"[*] TCP Iter {iteration}: No improvement vs overall max ({self.max_achieved_tp_overall:.2f} Mbps), no_gain_count {tcp_no_gain_count}/{self.config['tcp_no_gain_threshold']}.")

                    self.tcp_throughput_history.append(current_total_achieved_mbps)
                    if len(self.tcp_throughput_history) >= self.config["tcp_plateau_iterations"]:
                        if iteration >= (
                                self.config["tcp_initial_streams"] + self.config["tcp_plateau_iterations"] - 1):
                            min_hist_tp = min(self.tcp_throughput_history)
                            max_hist_tp = max(self.tcp_throughput_history)
                            ref_hist_tp = self.tcp_throughput_history[-1] if self.tcp_throughput_history else 0.1

                            if ref_hist_tp > 0.1:
                                percentage_diff = ((max_hist_tp - min_hist_tp) / ref_hist_tp) * 100
                                if self.verbose_logging:
                                    history_list_str = ", ".join([f"{x:.2f}" for x in self.tcp_throughput_history])
                                    self._log(
                                        f"[*] TCP Plateau Check: History=[{history_list_str}], Min={min_hist_tp:.2f}, Max={max_hist_tp:.2f}, Ref={ref_hist_tp:.2f}, Diff={percentage_diff:.2f}%")
                                if percentage_diff < self.config["tcp_plateau_percentage_variance"]:
                                    self._log(
                                        f"[*] TCP throughput plateau detected: Last {self.config['tcp_plateau_iterations']} iterations within {self.config['tcp_plateau_percentage_variance']:.2f}% variance.")
                                    self._conclude_test_and_summarize(f"TCP throughput plateaued (iter {iteration}).")
                                    return
                elif is_interrupt_error and self.verbose_logging:
                    self._log(
                        f"[*] Iter {iteration} (TCP): Test interrupted, throughput ({current_total_achieved_mbps:.2f} Mbps) not for max/plateau decision.")
                elif not is_interrupt_error and self.verbose_logging:
                    self._log(
                        f"[*] TCP Iter {iteration}: No valid throughput data (Total: {current_total_achieved_mbps:.2f} Mbps) for max/plateau consideration.")

                if tcp_no_gain_count >= self.config["tcp_no_gain_threshold"]:
                    self._conclude_test_and_summarize(
                        f"TCP no improvement vs max after {tcp_no_gain_count} attempts (iter {iteration}).")
                    return

                current_streams += self.config["tcp_streams_increment"]
                if current_streams > 128:
                    self._conclude_test_and_summarize(
                        f"TCP stream safety limit ({current_streams}) reached (iter {iteration}).")
                    return

            if self.stop_flag:
                if self.verbose_logging: self._log(f"[*] Iter {iteration}: Stop flag detected, breaking loop.")
                break
            time.sleep(self.config["main_loop_settle_s"])

        if not self.concluded_event.is_set():
            reason = "Test stopped by user or flag" if self.stop_flag else "Test iterations naturally completed"
            self._conclude_test_and_summarize(reason)

    def _start_latency_monitor(self):
        def monitor():
            pattern = re.compile(r"time=([\d.]+)\s*ms", re.IGNORECASE)
            avg_pattern = re.compile(r"min/avg/max(?:/mdev)?\s*=\s*[\d.]+/([\d.]+)/", re.IGNORECASE)

            while not self.latency_stop_flag and not self.stop_flag:
                latency_value = 9999.0
                try:
                    cmd = ["ping",
                           "-c", str(self.config["latency_ping_count"]),
                           "-W", str(self.config["latency_ping_packet_timeout_s"]),
                           self.remote_ip]
                    process_timeout = (self.config["latency_ping_packet_timeout_s"] * self.config[
                        "latency_ping_count"]) + 2
                    ping_proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               text=True, timeout=process_timeout, check=False)
                    output = ping_proc.stdout
                    avg_match = avg_pattern.search(output)
                    if avg_match:
                        latency_value = float(avg_match.group(1))
                    else:
                        matches = pattern.findall(output)
                        if matches:
                            latency_value = float(matches[-1])
                        elif "0% packet loss" not in output and \
                                ("100% packet loss" in output or ping_proc.returncode != 0):
                            if self.verbose_logging: self._log("[Latency] Ping timeout or 100% loss.")
                        elif "0% packet loss" in output and not matches and not avg_match:
                            if self.verbose_logging: self._log(
                                "[Latency] Ping successful but no RTT parsed from output.")
                            latency_value = 0.0
                except subprocess.TimeoutExpired:
                    if self.verbose_logging: self._log("[Latency] Ping command timed out.")
                except FileNotFoundError:
                    self._log("[!] Ping command not found. Latency monitoring will stop and record 0.")
                    self.latency_stop_flag = True
                    latency_value = 0.0
                except Exception as e:
                    if self.verbose_logging: self._log(f"[Latency] Error: {e}")

                self.data["latency"].append(latency_value)

                if self.config["latency_ping_interval_s"] > 0 and \
                        not self.latency_stop_flag and \
                        not self.stop_flag:
                    time.sleep(self.config["latency_ping_interval_s"])

        self.latency_thread = threading.Thread(target=monitor, daemon=True)
        self.latency_thread.start()

    def _conclude_test_and_summarize(self, reason):
        if self.concluded_event.is_set(): return
        self.concluded_event.set()

        self.stop_flag = True
        self.latency_stop_flag = True

        if self.latency_thread and self.latency_thread.is_alive() and threading.current_thread() != self.latency_thread:
            try:
                join_timeout = self.config["latency_ping_packet_timeout_s"] + self.config["latency_ping_interval_s"] + 1
                self.latency_thread.join(timeout=join_timeout)
                if self.latency_thread.is_alive() and self.verbose_logging:
                    self._log("[Warning] Latency thread join timed out during conclusion.")
            except Exception as e:
                if self.verbose_logging: self._log(f"[Warning] Error joining latency thread: {e}")

        self._log(f"\n-------- iPerf3 Test Concluded ({reason}) --------")
        self._final_summary_box()

        if self.verbose_logging: self._log("[*] Performing final iPerf process cleanup.")
        kill_iperf()

    def _final_summary_box(self):
        duration = int(time.time() - self.start_time) if self.start_time > 0 else 0
        if self.start_time == 0 and self.verbose_logging: self._log(
            "[Warning] Start time not set for summary duration.")
        mins, secs = divmod(duration, 60)
        box_width, content_width, key_text_max_width, separator = 70, 66, 30, " : "
        summary_lines = []

        def add_line_to_summary(key_text, value_text=""):
            if not key_text and not value_text: summary_lines.append(f"| {' ' * content_width} |"); return
            if not value_text:
                dt = key_text[:content_width];
                p_tot = content_width - len(dt);
                p_l = p_tot // 2
                summary_lines.append(f"| {' ' * p_l}{dt}{' ' * (p_tot - p_l)} |");
                return
            fk = key_text[:key_text_max_width].ljust(key_text_max_width)
            vpw = content_width - len(fk) - len(separator)
            fv = str(value_text)[:vpw if vpw > 0 else 0].ljust(vpw if vpw > 0 else 0)
            summary_lines.append(f"| {fk}{separator}{fv} |")

        title = f"iPerf3 {self.protocol} Test Summary ({self.direction})"
        border = f"+{'-' * (box_width - 2)}+"
        summary_lines.extend(["\n" + border, f"| {title.center(content_width)} |", border])
        add_line_to_summary("Test Duration", f"{mins:02}:{secs:02} (mm:ss)")
        add_line_to_summary("Profile", self.profile)
        add_line_to_summary("Packet Size", f"{self.packet_size} bytes" if self.packet_size else "Default")
        if self.protocol == "UDP": add_line_to_summary("UDP Loss Threshold Set", f"{self.loss_limit:.2f}%")
        add_line_to_summary("", "")
        add_line_to_summary("--- Max Achieved Stable Throughput ---")
        add_line_to_summary("Total Achieved", f"{self.max_achieved_tp_overall:.2f} Mbps")
        if self.direction == "Bi-Di":
            add_line_to_summary("  Tx (at Max Total)", f"{self.max_achieved_tp_tx:.2f} Mbps")
            add_line_to_summary("  Rx (at Max Total)", f"{self.max_achieved_tp_rx:.2f} Mbps")
        elif self.direction == "Uplink":
            add_line_to_summary("  Tx (at Max Total)", f"{self.max_achieved_tp_tx:.2f} Mbps")
        elif self.direction == "Downlink":
            add_line_to_summary("  Rx (at Max Total)", f"{self.max_achieved_tp_rx:.2f} Mbps")

        if self.protocol == "UDP": add_line_to_summary("  Target BW (at Max)",
                                                       f"{self.max_achieved_tp_udp_lossless_bw}M")
        add_line_to_summary("  Streams (at Max)", str(self.max_achieved_tp_streams))

        if self.data['latency']:
            valid_latencies = [l for l in self.data['latency'] if l is not None and l < 9999.0]
            avg_lat = sum(valid_latencies) / len(valid_latencies) if valid_latencies else 0.0
            min_lat = min(valid_latencies) if valid_latencies else 0.0
            all_recorded_latencies = [l for l in self.data['latency'] if l is not None]
            max_lat = max(all_recorded_latencies) if all_recorded_latencies else 0.0

            add_line_to_summary("", "");
            add_line_to_summary("--- Latency (Ping based) ---")
            add_line_to_summary("  Average ", f"{avg_lat:.2f} ms")
            add_line_to_summary("  Min ", f"{min_lat:.2f} ms")
            add_line_to_summary("  Max ", f"{max_lat:.2f} ms")

        if self.verbose_logging:
            last_tx = self.data['tx'][-1] if self.data['tx'] else 0.0
            last_rx = self.data['rx'][-1] if self.data['rx'] else 0.0
            total_live = self.data['throughput'][-1] if self.data['throughput'] else 0.0
            add_line_to_summary("", "");
            add_line_to_summary("--- Last Measured Live Data (Verbose) ---")
            add_line_to_summary("  Tx", f"{last_tx:.2f} Mbps");
            add_line_to_summary("  Rx", f"{last_rx:.2f} Mbps")
            add_line_to_summary("  Total Achieved", f"{total_live:.2f} Mbps")

        summary_lines.append(border)
        for line in summary_lines: self._log(line)

        if self.ui:
            try:
                if self.ui.get('start_button'): self.ui['start_button'].config(state="normal")
                if self.ui.get('stop_button'): self.ui['stop_button'].config(state="disabled")
                if self.ui.get('status_bar'): self.ui['status_bar'].config(text="Test Stopped/Completed")
                for btn_key in ['export_log_btn', 'save_tp_graph_btn', 'save_latency_graph_btn']:
                    if btn_key in self.ui and self.ui.get(btn_key): self.ui[btn_key].config(state="normal")
            except Exception as e:
                self._log(f"[UI Error] Failed to update UI elements state post-test: {e}")

    def _log(self, msg):
        if self.ui and self.ui.get('output_area'):
            try:
                output_area = self.ui['output_area']
                output_area.config(state='normal')
                output_area.insert("end", msg + "\n")
                output_area.config(state='disabled')
                output_area.see("end")
            except Exception as e:
                print(f"UI Logging Error: {e} | Message: {msg}")
        else:
            print(msg)