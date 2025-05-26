import subprocess
import threading
import json
import time
import re
from collections import deque


# from cleanup_utils import kill_iperf # Assuming this is in your project structure
# Mocking kill_iperf for standalone execution if needed for testing this class snippet
def kill_iperf():
    # In a real scenario, this would kill iperf3 processes.
    # For this snippet, it can be a placeholder.
    # print("[Debug] kill_iperf() called.")
    pass


class IperfTest:
    def __init__(self, remote_ip, iface, protocol, packet_size, direction, profile,
                 ui_refs, data_store, graph, update_metrics=None, verbose_logging=False):
        # ... (init remains largely the same as previous version) ...
        self.remote_ip = remote_ip
        self.iface = iface
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
            self._log(f"IperfTest (init): [Warning] Invalid or missing Loss Threshold. Defaulting to 1.0%.")
            self.loss_limit = 1.0

        self.thread = threading.Thread(target=self._run_test, daemon=True)
        self.config = {
            "iperf_test_duration_s": 5, "iperf_command_timeout_s": 25,
            "latency_ping_interval_s": 1, "latency_ping_packet_timeout_s": 1, "latency_ping_count": 1,
            "udp_initial_bw_mbps": 10,
            "udp_bw_step_mbps": {"Safe": 1, "Moderate": 10, "Aggressive": 30, "Default": 10},
            "udp_min_bw_for_binary_search_low_bound_mbps": 1,
            "udp_bw_confirmation_runs_after_pass": 1,  # This enables confirmations
            "udp_initial_streams": 2,
            "udp_streams_increment_step": {"Safe": 1, "Moderate": 1, "Aggressive": 2, "Default": 1},
            "udp_max_loss_retry_attempts": 1,
            "udp_no_gain_threshold_after_bw_freeze": 2,
            "tcp_initial_streams": 2, "tcp_streams_increment": 1, "tcp_no_gain_threshold": 5,
            "tcp_plateau_iterations": 3, "tcp_plateau_percentage_variance": 3.0,
            "main_loop_settle_s": 0.5, "process_kill_timeout_s": 2,
            "iperf_execution_max_internal_retries": 2, "iperf_execution_retry_delay_s": 1,
        }
        for key in ["udp_bw_step_mbps", "udp_streams_increment_step"]:
            if "Default" not in self.config[key]:
                self.config[key]["Default"] = self.config[key].get("Moderate", 10 if "bw" in key else 1)

        self.tcp_throughput_history = deque(maxlen=self.config["tcp_plateau_iterations"])
        self.max_achieved_tp_overall = 0.0;
        self.max_achieved_tp_tx = 0.0;
        self.max_achieved_tp_rx = 0.0
        self.max_achieved_tp_udp_lossless_bw = 0;
        self.max_achieved_tp_streams = 0
        self.last_reverted_lossy_info = {}

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
            kill_iperf()  # Ensure iperf is killed if stop is called multiple times
            return
        self._log(f"[*] Test stopping: {reason}")
        self.stop_flag = True
        self.latency_stop_flag = True
        if self.verbose_logging: self._log("[*] Attempting to kill iPerf processes due to user stop request...")
        kill_iperf()

    def _execute_single_iperf_test(self, iteration_label, current_udp_bw_mbps_test, current_streams_test):
        # Determine if JSON output should be used
        use_json_output = not (self.protocol == "UDP" and self.direction == "Downlink")

        cmd = ["iperf3", "-c", self.remote_ip,
               "-t", str(self.config["iperf_test_duration_s"]),
               "-P", str(current_streams_test)]

        if use_json_output:
            cmd.append("-J")
        # For UDP Downlink with human-readable output, we still need server output
        # if certain server-side details were ever to be parsed from human-readable,
        # but the current request is for the client's [SUM] receiver line.
        # The --get-server-output flag primarily affects JSON output by embedding server's JSON.
        # For human-readable, server stats are usually separate lines if iperf server is run with -s.
        # The client command output itself contains the receiver summary.
        # The original condition was:
        # if self.protocol == "TCP" or (self.protocol == "UDP" and self.direction == "Downlink"):
        # For human-readable UDP Downlink, this isn't strictly necessary for the requested parsing,
        # but keeping it won't hurt if it was intended for other reasons or future parsing.
        # However, if it causes issues with human-readable parsing (e.g. by adding unwanted server text before client sum),
        # it might need adjustment. For now, keeping original logic for this flag.
        if self.protocol == "TCP" or \
                (
                        self.protocol == "UDP" and self.direction == "Downlink" and use_json_output):  # Only get server output if JSON is expected for UDP Downlink
            cmd.append("--get-server-output")
        elif self.protocol == "TCP":  # Ensure TCP always gets it if the above condition isn't met
            cmd.append("--get-server-output")

        if self.packet_size: cmd.extend(["-l", str(self.packet_size)])

        bw_for_cmd = 0
        if self.protocol == "UDP":
            bw_for_cmd = int(round(current_udp_bw_mbps_test))
            cmd.extend(["-u", "-b", f"{bw_for_cmd}M"])

        if self.direction == "Downlink":
            cmd.append("-R")
        elif self.direction == "Bi-Di":
            cmd.append("--bidir")

        if self.verbose_logging: self._log(f"[*] Executing (Iter {iteration_label}): {' '.join(cmd)}")

        iperf_output_str = ""
        proc_returncode = -1
        result_json = None  # Will remain None if not use_json_output
        tx_mbps, rx_mbps, total_achieved_mbps, loss_percent = 0.0, 0.0, 0.0, 100.0
        log_source_tx, log_source_rx = "(NoData)", "(NoData)"
        used_server_perspective_for_tx = False
        critical_error_str = None
        is_interrupt = False
        is_retryable_json_error = False

        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  text=True, timeout=self.config["iperf_command_timeout_s"], check=False)
            iperf_output_str = proc.stdout
            proc_returncode = proc.returncode
            if proc_returncode != 0 and self.verbose_logging and not self.stop_flag:
                self._log(
                    f"[V][Warning] iPerf3 process (Iter {iteration_label}) exited code {proc_returncode}. Output: {iperf_output_str[:200]}")

            if use_json_output and iperf_output_str:  # Try to parse JSON only if expected
                json_start_index = iperf_output_str.find('{')
                if json_start_index != -1:
                    json_data_str = iperf_output_str[json_start_index:]
                    result_json = json.loads(json_data_str)
                    if result_json and self.protocol == "TCP" and self.verbose_logging:
                        self._log(
                            f"[V] Iter {iteration_label} RAW JSON (End section for TCP): {json.dumps(result_json.get('end', {}), indent=2)}")
                elif self.verbose_logging and not self.stop_flag:
                    self._log(
                        f"[V][Warning] No JSON start in iPerf3 output (Iter {iteration_label}) when JSON was expected. Output: {iperf_output_str[:200]}")
            elif not use_json_output and self.verbose_logging and iperf_output_str.strip().startswith("{"):
                self._log(
                    f"[V][Info] Iter {iteration_label}: JSON output detected but human-readable was expected (UDP Downlink). Will parse human summary.")

            if not iperf_output_str and self.verbose_logging and not self.stop_flag:
                self._log(f"[V][Warning] No output from iPerf3 (Iter {iteration_label}). RC: {proc_returncode}")

        except subprocess.TimeoutExpired:
            if self.stop_flag:
                if self.verbose_logging: self._log(f"[*] iPerf3 (Iter {iteration_label}) timed out during stop.")
            else:
                self._log(f"[!] iPerf3 command (Iter {iteration_label}) timed out.");
                critical_error_str = f"iPerf3 timeout (Iter {iteration_label})"
            # Ensure return values reflect failure
            return 0.0, 0.0, 0.0, 100.0, None, critical_error_str, not self.stop_flag, used_server_perspective_for_tx, log_source_tx, log_source_rx, is_retryable_json_error
        except FileNotFoundError:
            self._log("[!] iPerf3 command not found.");
            critical_error_str = "iPerf3 not found"
            return 0.0, 0.0, 0.0, 100.0, None, critical_error_str, False, used_server_perspective_for_tx, log_source_tx, log_source_rx, is_retryable_json_error
        except json.JSONDecodeError as e:  # This can only happen if use_json_output was True
            if not self.stop_flag:
                is_retryable_json_error = True
                if self.verbose_logging: self._log(
                    f"[V] Failed to parse iPerf3 JSON (Iter {iteration_label}): {e}. Output: {iperf_output_str[:500]}")
        except Exception as e:
            if not self.stop_flag:
                self._log(f"[!] Error during iPerf3 execution/parsing (Iter {iteration_label}): {e}")
                critical_error_str = f"Unexpected iPerf3 execution error (Iter {iteration_label}): {e}"

        # Check for iperf "error" field in JSON if JSON was parsed
        if use_json_output and result_json:
            iperf_error_from_json = result_json.get("error")
            if iperf_error_from_json:
                is_user_interrupt_error = (
                        "interrupt" in iperf_error_from_json.lower() or "terminated" in iperf_error_from_json.lower() or "client has exited" in iperf_error_from_json.lower() or "user stop" in iperf_error_from_json.lower())
                if is_user_interrupt_error:
                    is_interrupt = True
                    if self.verbose_logging: self._log(
                        f"[*] iPerf3 (Iter {iteration_label}) interrupted by client/user (JSON error): {iperf_error_from_json}")
                elif not self.stop_flag:
                    self._log(f"[!] iPerf3 reported error in JSON (Iter {iteration_label}): {iperf_error_from_json}")
                if not is_user_interrupt_error and not self.stop_flag and (
                        "parameter conflict" in iperf_error_from_json.lower() or "reverse mode conflict" in iperf_error_from_json.lower() or (
                        (
                                "unable to connect" in iperf_error_from_json.lower() or "connection refused" in iperf_error_from_json.lower()) and proc_returncode != 0)):
                    critical_error_str = f"iPerf3 critical error: {iperf_error_from_json} (Iter {iteration_label})"
                    return tx_mbps, rx_mbps, total_achieved_mbps, loss_percent, result_json, critical_error_str, is_interrupt, used_server_perspective_for_tx, log_source_tx, log_source_rx, is_retryable_json_error

        # Check for common text errors if no JSON or JSON error didn't cover it
        if (
                not result_json and use_json_output) or not use_json_output:  # Check text output if JSON not primary source or not used
            if "connection refused" in iperf_output_str.lower() or \
                    "unable to connect" in iperf_output_str.lower() or \
                    "iperf3: error - " in iperf_output_str.lower() and "interrupt" not in iperf_output_str.lower():  # Common iperf3 CLI errors
                # Distinguish user interrupt from other errors
                is_user_interrupt_text = "interrupt" in iperf_output_str.lower() or "terminated" in iperf_output_str.lower() or "user stop" in iperf_output_str.lower()
                if is_user_interrupt_text:
                    is_interrupt = True
                    if self.verbose_logging: self._log(
                        f"[*] iPerf3 (Iter {iteration_label}) interrupted (text error): {iperf_output_str[:100].strip()}")
                elif not self.stop_flag:
                    extracted_error = iperf_output_str.lower().split("iperf3: error -", 1)[
                        -1].strip() if "iperf3: error -" in iperf_output_str.lower() else "iPerf3 connection/other text error"
                    critical_error_str = f"{extracted_error} (Iter {iteration_label})"
                    self._log(f"[!] {critical_error_str}: {iperf_output_str[:200]}")
                if critical_error_str and not is_interrupt:
                    return tx_mbps, rx_mbps, total_achieved_mbps, loss_percent, None, critical_error_str, is_interrupt, used_server_perspective_for_tx, log_source_tx, log_source_rx, is_retryable_json_error

        # === METRIC EXTRACTION LOGIC ===
        if critical_error_str:  # If any critical error was set above
            return tx_mbps, rx_mbps, total_achieved_mbps, loss_percent, result_json, critical_error_str, is_interrupt, used_server_perspective_for_tx, log_source_tx, log_source_rx, is_retryable_json_error

        if is_interrupt:  # If test was stopped (either from JSON error or text error)
            # Return current (likely default/zero) values; main loop will handle stop.
            return tx_mbps, rx_mbps, total_achieved_mbps, loss_percent, result_json, critical_error_str, True, used_server_perspective_for_tx, log_source_tx, log_source_rx, is_retryable_json_error

        if not use_json_output:  # UDP Downlink - Human Readable Path
            parsed_human_success = False
            # Target line: [SUM]   0.00-5.00   sec  16.8 MBytes  28.1 Mbits/sec  0.591 ms  132/12678 (0.47%)  receiver
            human_rx_sum_pattern = re.compile(
                r"\[SUM\]\s+[\d\.-]+\s*sec\s+"  # Interval part e.g., 0.00-5.00 sec or 0.00-5.25 sec
                r"[\d\.]+\s+MBytes\s+"  # Transfer: e.g., 16.8 MBytes (Ignored for now)
                r"([\d\.]+)\s+Mbits/sec\s+"  # Bitrate: e.g., 28.1 (Group 1)
                r"[\d\.]+\s+ms\s+"  # Jitter: e.g., 0.591 ms (Ignored for now)
                r"(\d+)/(\d+)\s+"  # Lost/Total: e.g., 132 (Group 2) / 12678 (Group 3)
                r"\(([\d\.]+)\%\)\s*receiver"  # Loss Percent: e.g., 0.47 (Group 4)
            )
            match_obj = None
            # Search for the SUM receiver line. It's typically near the end.
            # Iterate reversed lines to find the best candidate for the final summary.
            for line in reversed(iperf_output_str.splitlines()):
                if "[SUM]" in line and "receiver" in line and "Mbits/sec" in line and "%" in line:
                    m = human_rx_sum_pattern.search(line)
                    if m:
                        match_obj = m
                        break  # Found a sum receiver line matching the pattern

            if match_obj:
                parsed_bitrate_mbps = float(match_obj.group(1))
                # lost_dgrams = int(match_obj.group(2))
                # total_dgrams = int(match_obj.group(3))
                parsed_loss_percent = float(match_obj.group(4))  # Use the directly reported percentage

                loss_percent = parsed_loss_percent
                rx_mbps = parsed_bitrate_mbps
                tx_mbps = 0.0  # Downlink
                total_achieved_mbps = rx_mbps
                log_source_rx = "(Human SUM)"
                parsed_human_success = True
                if self.verbose_logging:
                    self._log(
                        f"[V] UDP Downlink (Human Parse): Rate={rx_mbps:.2f} Mbps, Loss={loss_percent:.2f}% from line: {match_obj.group(0).strip()}")

            if not parsed_human_success and not self.stop_flag:  # if parsing failed and not due to stop
                if self.verbose_logging: self._log(
                    f"[V] Iter {iteration_label}: Failed to parse human-readable UDP Downlink summary. Output: {iperf_output_str[:500]}. Defaulting to 100% loss.")
                # tx_mbps, rx_mbps, total_achieved_mbps are already 0.0, loss_percent is 100.0 (from func start)

        elif use_json_output and result_json and not is_retryable_json_error:
            # JSON Path for TCP, UDP Uplink, UDP Bi-Di
            if self.protocol == "UDP":  # UDP Uplink or Bi-Di
                client_end_sum = result_json.get("end", {}).get("sum", {})
                client_received_actual_mbps = 0.0
                loss_percent_reported_by_client = 100.0

                if client_end_sum:
                    client_sum_bytes = float(client_end_sum.get("bytes", 0))
                    # Get actual duration from JSON test_start if end.sum.seconds is missing/zero
                    actual_duration = float(client_end_sum.get("seconds", 0))
                    if actual_duration <= 0:
                        actual_duration = float(result_json.get("start", {}).get("test_start", {}).get("duration",
                                                                                                       self.config[
                                                                                                           "iperf_test_duration_s"]))
                    if actual_duration <= 0:  # Ultimate fallback
                        actual_duration = self.config["iperf_test_duration_s"]

                    loss_percent_reported_by_client = float(client_end_sum.get("lost_percent", 100.0))
                    if actual_duration > 0:
                        client_perspective_bps = (client_sum_bytes * 8) / actual_duration
                        client_received_actual_mbps = client_perspective_bps / 1_000_000.0

                if loss_percent_reported_by_client >= 100.0: client_received_actual_mbps = 0.0

                # Note: The "if self.direction == 'Downlink':" block from your original code
                # that was here is now effectively dead code for UDP, because if it's UDP Downlink,
                # 'use_json_output' would be False, and this entire 'elif use_json_output...' block
                # would not be entered for metric calculation. It would take the human-readable path.

                if self.direction == "Uplink":  # UDP Uplink from JSON (working as expected by user)
                    # For UDP Uplink, client_end_sum typically contains server's receiver stats
                    # So client_received_actual_mbps effectively represents server_received_mbps
                    total_achieved_mbps = client_received_actual_mbps
                    tx_mbps = total_achieved_mbps
                    rx_mbps = 0.0
                    loss_percent = loss_percent_reported_by_client
                    if self.verbose_logging: self._log(
                        f"[V] UDP Uplink (JSON): Server RCVR stats via client JSON sum: {tx_mbps:.2f} Mbps, Loss: {loss_percent:.2f}%")
                elif self.direction == "Bi-Di":  # UDP Bi-Di from JSON (simplified, as per original)
                    # User's original code for UDP BiDi took client_received_actual_mbps as total.
                    # This interpretation is kept as "don't touch uplink part" implies minimal changes to other UDP JSON.
                    total_achieved_mbps = client_received_actual_mbps
                    tx_mbps = total_achieved_mbps  # This is a simplification for BiDi.
                    rx_mbps = 0.0  # A full BiDi parse would sum streams.
                    loss_percent = loss_percent_reported_by_client
                    if self.verbose_logging: self._log(
                        f"[V] UDP Bi-Di (JSON): Stats from client JSON sum (simplified): {total_achieved_mbps:.2f} Mbps, Loss: {loss_percent:.2f}%")

                if loss_percent >= 100.0:  # Common adjustment for UDP
                    total_achieved_mbps = 0.0
                    if self.direction == "Uplink":
                        tx_mbps = 0.0
                    elif self.direction == "Bi-Di":
                        tx_mbps = 0.0; rx_mbps = 0.0

            else:  # TCP Parsing (remains unchanged from user's code)
                end_data = result_json.get("end", {})
                sum_sent_data_last = end_data.get("sum_sent", {})
                sum_received_data_last = end_data.get("sum_received", {})
                uplink_bps_to_use, downlink_bps_to_use = 0, 0

                if self.direction == "Uplink":
                    server_output_json = result_json.get("server_output_json", {})
                    server_sum_received = server_output_json.get("end", {}).get("sum_received", {})
                    if server_sum_received and "bits_per_second" in server_sum_received:
                        uplink_bps_to_use = server_sum_received.get("bits_per_second", 0)
                        log_source_tx, used_server_perspective_for_tx = "(SrvRcv via SrvOutput)", True
                    elif sum_received_data_last and "bits_per_second" in sum_received_data_last:
                        uplink_bps_to_use = sum_received_data_last.get("bits_per_second", 0)
                        log_source_tx, used_server_perspective_for_tx = "(SrvRcv via CliSumRcv)", True
                    elif sum_sent_data_last and "bits_per_second" in sum_sent_data_last:
                        uplink_bps_to_use = sum_sent_data_last.get("bits_per_second", 0)
                        log_source_tx = "(CliSent - fallback)"
                    tx_mbps = uplink_bps_to_use / 1_000_000.0;
                    rx_mbps = 0.0
                elif self.direction == "Downlink":
                    if sum_received_data_last and "bits_per_second" in sum_received_data_last:
                        downlink_bps_to_use = sum_received_data_last.get("bits_per_second", 0)
                        log_source_rx = "(CliRcv)"
                    tx_mbps = 0.0;
                    rx_mbps = downlink_bps_to_use / 1_000_000.0
                    log_source_tx = "(N/A)"
                elif self.direction == "Bi-Di":
                    calculated_uplink_bps, calculated_downlink_bps = 0, 0
                    end_streams_data = end_data.get("streams", [])
                    num_tx_streams_processed, num_rx_streams_processed = 0, 0
                    if end_streams_data:
                        for stream_report in end_streams_data:
                            stream_bps = stream_report.get("receiver", {}).get("bits_per_second", 0)
                            if stream_report.get("sender", {}).get("sender", False):
                                calculated_uplink_bps += stream_bps;
                                num_tx_streams_processed += 1
                            else:
                                calculated_downlink_bps += stream_bps;
                                num_rx_streams_processed += 1
                        if num_tx_streams_processed > 0: log_source_tx = f"(Sum {num_tx_streams_processed} SrvRcv streams)"; used_server_perspective_for_tx = True
                        if num_rx_streams_processed > 0: log_source_rx = f"(Sum {num_rx_streams_processed} CliRcv streams)"
                    else:
                        server_output_json = result_json.get("server_output_json", {})
                        server_sum_end = server_output_json.get("end", {})
                        server_sum_sent_bidir = server_sum_end.get("sum_sent", {})
                        server_sum_rcvd_bidir = server_sum_end.get("sum_received", {})

                        if server_sum_rcvd_bidir and "bits_per_second" in server_sum_rcvd_bidir:
                            calculated_uplink_bps = server_sum_rcvd_bidir.get("bits_per_second", 0)
                            log_source_tx = "(SrvRcv from SrvOut.SumRcvd)";
                            used_server_perspective_for_tx = True
                        elif sum_sent_data_last and "bits_per_second" in sum_sent_data_last:
                            calculated_uplink_bps = sum_sent_data_last.get("bits_per_second", 0)
                            log_source_tx = "(CliSent Sum - fallback)"

                        if sum_received_data_last and "bits_per_second" in sum_received_data_last:
                            calculated_downlink_bps = sum_received_data_last.get("bits_per_second", 0)
                            log_source_rx = "(CliRcv Sum)"
                        elif server_sum_sent_bidir and "bits_per_second" in server_sum_sent_bidir:
                            calculated_downlink_bps = server_sum_sent_bidir.get("bits_per_second", 0)
                            log_source_rx = "(SrvSent from SrvOut.SumSent)"
                    tx_mbps = calculated_uplink_bps / 1_000_000.0
                    rx_mbps = calculated_downlink_bps / 1_000_000.0
                total_achieved_mbps = tx_mbps + rx_mbps
                loss_percent = 0.0  # TCP doesn't report loss this way in iperf3 sum

        elif use_json_output and self.protocol == "UDP" and not self.stop_flag and not is_interrupt:
            # This case: JSON was expected for UDP (Uplink/Bi-Di), but result_json is None or
            # is_retryable_json_error was True but we are past retry stage (or it means no valid JSON object at all).
            # Ensure defaults for 0 TP / 100% loss are used.
            if not is_retryable_json_error:  # Only log if it's a definitive failure for this attempt
                if self.verbose_logging: self._log(
                    f"[V] Iter {iteration_label}: No or invalid JSON for UDP {self.direction} (UL/Bi-Di). Assuming 100% loss.")
            # tx_mbps, rx_mbps, total_achieved_mbps already 0.0, loss_percent is 100.0 (from func start)

        elif not self.stop_flag and not is_interrupt:  # Catch-all for other unparsed scenarios
            if self.verbose_logging: self._log(
                f"[V] Iter {iteration_label}: Could not parse results. Assuming 0 TP, 100% Loss.")
            # Defaults are already set.

        return tx_mbps, rx_mbps, total_achieved_mbps, loss_percent, result_json, critical_error_str, is_interrupt, used_server_perspective_for_tx, log_source_tx, log_source_rx, is_retryable_json_error

    def _run_test(self):
        self.start_time = time.time()
        self.concluded_event.clear()
        self.tcp_throughput_history.clear()
        self.last_reverted_lossy_info.clear()  # Clear for new test run

        self._log(
            f"\n======================= iPerf3 {self.protocol} Test ({self.direction}) Started at {time.strftime('%H:%M:%S')} =======================")
        if self.verbose_logging:
            self._log(
                f"[*] Profile: {self.profile}, Target IP: {self.remote_ip}, Packet Size: {self.packet_size if self.packet_size else 'Default'}")

        self._start_latency_monitor()

        current_streams = 0;
        current_udp_bw_mbps = 0
        udp_bw_for_stream_push_phase = 0;
        udp_bw_coarse_step = 0;
        udp_stream_step = 0
        udp_bw_frozen = False;
        udp_coarse_search_phase = False;
        udp_binary_fine_tuning_phase = False
        udp_bs_low_bound = 0;
        udp_bs_high_bound = float('inf')
        udp_last_lossless_target_bw = 0
        udp_loss_retry_count = 0
        udp_no_gain_stream_phase_count = 0
        min_practical_bw = 1
        tcp_no_gain_count = 0
        iteration_num = 0

        MAX_IPERF_EXECUTION_RETRIES = self.config.get("iperf_execution_max_internal_retries", 2)
        IPERF_EXECUTION_RETRY_DELAY_S = self.config.get("iperf_execution_retry_delay_s", 1)

        if self.protocol == "UDP":
            self._log(f"[*] UDP Loss Threshold: {self.loss_limit:.2f}%")
            if self.verbose_logging:
                self._log(
                    f"[V] UDP Config: Confirmation runs: {self.config.get('udp_bw_confirmation_runs_after_pass', 0)}, Retries on fail: {self.config['udp_max_loss_retry_attempts']}")
                self._log(
                    f"[V] UDP Config: No-gain threshold for stream push: {self.config['udp_no_gain_threshold_after_bw_freeze']}")
                self._log(
                    f"[V] UDP Config: Saturation Factor: {self.config.get('udp_saturation_threshold_factor', 0.85) * 100:.0f}%, Min Aggregate TP for Sat Check: {self.config.get('udp_min_significant_aggregate_tp_for_saturation_check_mbps', 10.0)} Mbps")
            current_streams = self.config["udp_initial_streams"]
            current_udp_bw_mbps = int(self.config["udp_initial_bw_mbps"])
            udp_bw_coarse_step = int(
                self.config["udp_bw_step_mbps"].get(self.profile, self.config["udp_bw_step_mbps"]["Default"]))
            udp_stream_step = self.config["udp_streams_increment_step"].get(self.profile,
                                                                            self.config["udp_streams_increment_step"][
                                                                                "Default"])
            min_practical_bw = int(self.config.get("udp_min_bw_for_binary_search_low_bound_mbps", 1))
            udp_coarse_search_phase = True
        else:  # TCP
            current_streams = self.config["tcp_initial_streams"]

        while not self.stop_flag:
            iteration_num += 1
            current_latency = self.data["latency"][-1] if self.data["latency"] else 0.0

            if self.protocol == "UDP":  # BW recovery logic
                current_udp_bw_mbps = int(round(current_udp_bw_mbps))
                if current_udp_bw_mbps <= 0:
                    recovered_bw = udp_last_lossless_target_bw if udp_last_lossless_target_bw > 0 else min_practical_bw
                    if self.verbose_logging or current_udp_bw_mbps != recovered_bw:
                        self._log(
                            f"[V] UDP BW became non-positive ({current_udp_bw_mbps}M), adjusted to {recovered_bw}M/stream for iter {iteration_num}.")
                    current_udp_bw_mbps = recovered_bw
                    if current_udp_bw_mbps <= 0:
                        self._log(
                            f"[!] UDP BW target critically non-positive ({current_udp_bw_mbps}M/stream). Concluding.")
                        self._conclude_test_and_summarize("UDP BW error (non-positive)");
                        return

            current_params_successful_execution = False;
            exec_results = None  # To store tuple from _execute_single_iperf_test
            current_tx_mbps, current_rx_mbps, current_total_achieved_mbps, current_loss_percent = 0.0, 0.0, 0.0, 100.0
            _result_json_from_exec = None;
            critical_error_from_exec = None;
            is_interrupt_error_from_exec = False
            used_server_perspective_for_tx_tcp_from_exec = False;
            log_source_tx_tcp_from_exec = "(NoData)";
            log_source_rx_tcp_from_exec = "(NoData)"
            is_retryable_json_error_from_exec = False

            for attempt_num in range(MAX_IPERF_EXECUTION_RETRIES + 1):
                effective_iter_label = f"{iteration_num}"
                if attempt_num > 0: effective_iter_label = f"{iteration_num}.R{attempt_num}"

                # Unpack all expected values from the execution method
                current_tx_mbps, current_rx_mbps, current_total_achieved_mbps, current_loss_percent, \
                    _result_json_from_exec, critical_error_from_exec, is_interrupt_error_from_exec, \
                    used_server_perspective_for_tx_tcp_from_exec, log_source_tx_tcp_from_exec, log_source_rx_tcp_from_exec, \
                    is_retryable_json_error_from_exec = self._execute_single_iperf_test(effective_iter_label,
                                                                                        current_udp_bw_mbps,
                                                                                        current_streams)

                exec_results = (  # Reconstruct tuple for logging if needed, though direct vars are used now
                    current_tx_mbps, current_rx_mbps, current_total_achieved_mbps, current_loss_percent,
                    _result_json_from_exec, critical_error_from_exec, is_interrupt_error_from_exec,
                    used_server_perspective_for_tx_tcp_from_exec, log_source_tx_tcp_from_exec,
                    log_source_rx_tcp_from_exec,
                    is_retryable_json_error_from_exec
                )

                if critical_error_from_exec: self._conclude_test_and_summarize(critical_error_from_exec); return
                if self.stop_flag and is_interrupt_error_from_exec:
                    if self.verbose_logging: self._log(
                        f"[*] Iter {effective_iter_label}: Test execution interrupted by stop signal, breaking from retries.")
                    break  # Break from retry loop
                if is_retryable_json_error_from_exec:  # Only retry on this specific flag
                    if attempt_num < MAX_IPERF_EXECUTION_RETRIES:
                        if self.verbose_logging: self._log(
                            f"[V] iPerf3 execution/parsing failed for iteration {iteration_num} (attempt {attempt_num + 1}/{MAX_IPERF_EXECUTION_RETRIES + 1}). Retrying in {IPERF_EXECUTION_RETRY_DELAY_S}s...")
                        time.sleep(IPERF_EXECUTION_RETRY_DELAY_S);
                        if self.stop_flag:  # Check stop flag again after sleep
                            if self.verbose_logging: self._log(
                                f"[*] Stop flag detected after retry delay for iter {effective_iter_label}.")
                            break
                        continue  # To next attempt
                    else:  # All retries exhausted for retryable error
                        self._log(
                            f"[!] Persistent iPerf3 execution error for iteration {iteration_num} parameters after {MAX_IPERF_EXECUTION_RETRIES + 1} attempts. Stopping test.")
                        self._conclude_test_and_summarize("Persistent iPerf3 error (e.g., JSON parsing / broken pipe)");
                        return

                # If not a critical error, not an interrupt during stop, and not a retryable error (or retries succeeded)
                current_params_successful_execution = True;  # Successfully executed (though results might be 0 TP / 100% loss)
                break  # Break from retry loop

            if self.stop_flag:  # Check stop flag after retry loop (in case it was set during retries/sleep)
                if self.verbose_logging: self._log(
                    f"[*] Iter {iteration_num}: Stop flag detected, breaking main test loop.")
                break

            if not current_params_successful_execution:  # Should not be reached if logic above is correct
                if self.verbose_logging: self._log(
                    f"[V] Failed to get valid results for iteration {iteration_num} parameters after all attempts. This state should ideally not be reached.")
                self._conclude_test_and_summarize(f"iPerf execution failed for iter {iteration_num}");  # Fallback
                return

            # Metrics (current_tx_mbps etc.) are now set from the last successful execution attempt or final failed attempt values

            log_msg_detail = ""  # Logging results
            if self.protocol == "UDP":
                log_msg = (
                    f"UDP BW: {current_udp_bw_mbps:<3}M | Streams: {current_streams:<2} | Tx: {current_tx_mbps:6.2f} Mbps | Rx: {current_rx_mbps:6.2f} Mbps | Total: {current_total_achieved_mbps:6.2f} Mbps | Loss: {current_loss_percent:5.2f}%")
            else:  # TCP
                if self.verbose_logging:
                    details = []
                    if self.direction != "Downlink" and log_source_tx_tcp_from_exec not in ["(NoData)",
                                                                                            "(N/A)"]: details.append(
                        f"TxSrc:{log_source_tx_tcp_from_exec}")
                    if self.direction != "Uplink" and log_source_rx_tcp_from_exec not in ["(NoData)",
                                                                                          "(N/A)"]: details.append(
                        f"RxSrc:{log_source_rx_tcp_from_exec}")
                    if details: log_msg_detail = " (" + ", ".join(details) + ")"
                log_msg = (
                    f"TCP Streams: {current_streams:<2} | Tx: {current_tx_mbps:7.2f} Mbps | Rx: {current_rx_mbps:7.2f} Mbps | Total: {current_total_achieved_mbps:7.2f} Mbps{log_msg_detail}")
            self._log(log_msg)

            timestamp = time.time()  # Store and update metrics/graphs
            self.data["local_tx"].append(current_tx_mbps);
            self.data["local_rx"].append(current_rx_mbps)
            self.data["throughput"].append(current_total_achieved_mbps);
            self.data["timestamp"].append(timestamp)
            if self.update_metrics: self.update_metrics(tx=current_tx_mbps, rx=current_rx_mbps, latency=current_latency,
                                                        loss=current_loss_percent if self.protocol == "UDP" else 0.0,
                                                        duration_secs=int(timestamp - self.start_time),
                                                        total=current_total_achieved_mbps)
            # In IperfTest._run_test
            if self.graph:
                self.graph.update_graphs(
                    self.data.get("timestamp", []),
                    self.data.get("local_tx", []),  # Use "local_tx"
                    self.data.get("local_rx", []),  # Use "local_rx"
                    self.data.get("latency", [])
                )

            # --- Decision Logic ---
            # (Your existing extensive decision logic for UDP and TCP follows)
            # This logic uses current_total_achieved_mbps, current_loss_percent etc.
            # which are now correctly populated by the modified _execute_single_iperf_test
            if self.protocol == "UDP":
                new_overall_max_found_this_iter = False
                current_test_initial_pass = (current_loss_percent <= self.loss_limit)
                is_stable_and_confirmed_pass = current_test_initial_pass
                udp_point_saturated_this_iteration = False

                if current_test_initial_pass and \
                        udp_binary_fine_tuning_phase and \
                        self.config.get("udp_bw_confirmation_runs_after_pass", 0) > 0:
                    num_conf_runs = self.config["udp_bw_confirmation_runs_after_pass"]
                    if self.verbose_logging: self._log(
                        f"[*] UDP Binary Fine-Tuning: Initial pass at {current_udp_bw_mbps}M/stream. Performing {num_conf_runs} confirmation run(s).")
                    for i_confirm in range(num_conf_runs):
                        if self.stop_flag: break
                        conf_iteration_label = f"{iteration_num}.C{i_confirm + 1}"
                        _ct_tx, _ct_rx, conf_total_tp, conf_loss, _, conf_crit_err, conf_is_intr, _, _, _, conf_is_retryable_err = \
                            self._execute_single_iperf_test(conf_iteration_label, current_udp_bw_mbps, current_streams)
                        if conf_crit_err: self._conclude_test_and_summarize(
                            f"Critical error during UDP confirmation: {conf_crit_err}"); return
                        if self.stop_flag and conf_is_intr: break
                        if conf_is_retryable_err:  # If confirmation run itself had a retryable execution error
                            if self.verbose_logging: self._log(
                                f"[V] UDP Conf. {i_confirm + 1} for {current_udp_bw_mbps}M/stream had execution issue. Marking as fail for confirmation.")
                            is_stable_and_confirmed_pass = False;
                            current_loss_percent = 101.0  # Indicate failure due to execution for decision logic
                            break  # Stop confirmation attempts
                        self._log(
                            f"UDP BW: {current_udp_bw_mbps:<3}M | Streams: {current_streams:<2} | (Conf. {i_confirm + 1}) TP: {conf_total_tp:.2f} Mbps | Loss: {conf_loss:5.2f}%")
                        if conf_loss > self.loss_limit:
                            is_stable_and_confirmed_pass = False;
                            current_loss_percent = conf_loss  # Update current_loss_percent to the failing confirmation loss
                            self._log(
                                f"[!] UDP Binary Fine-Tuning: {current_udp_bw_mbps}M/stream failed confirmation run {i_confirm + 1} (Loss: {conf_loss:.2f}%).")
                            break
                    if self.stop_flag: break

                if is_stable_and_confirmed_pass:  # This now means initial pass was good AND all confirmations (if any) passed
                    udp_loss_retry_count = 0
                    udp_last_lossless_target_bw = current_udp_bw_mbps
                    # Check if current_total_achieved_mbps is not from an interrupted/stopped test before updating max
                    if not is_interrupt_error_from_exec and current_total_achieved_mbps > self.max_achieved_tp_overall:
                        self.max_achieved_tp_overall, self.max_achieved_tp_tx, self.max_achieved_tp_rx = current_total_achieved_mbps, current_tx_mbps, current_rx_mbps
                        self.max_achieved_tp_udp_lossless_bw = current_udp_bw_mbps
                        self.max_achieved_tp_streams = current_streams
                        new_overall_max_found_this_iter = True;
                        udp_no_gain_stream_phase_count = 0
                        if self.verbose_logging: self._log(
                            f"[*] New max UDP throughput: {self.max_achieved_tp_overall:.2f} Mbps at {current_udp_bw_mbps}M/stream target, {current_streams} streams")

                    attempted_aggregate_target_bw = float(current_udp_bw_mbps * current_streams)
                    saturation_factor = self.config.get('udp_saturation_threshold_factor', 0.85)
                    min_agg_tp_for_sat_check = self.config.get(
                        'udp_min_significant_aggregate_tp_for_saturation_check_mbps', 10.0)
                    if attempted_aggregate_target_bw >= min_agg_tp_for_sat_check and attempted_aggregate_target_bw > 0:
                        if (current_total_achieved_mbps / attempted_aggregate_target_bw) < saturation_factor:
                            udp_point_saturated_this_iteration = True
                            if self.verbose_logging: self._log(
                                f"[*] UDP Saturation Alert: Achieved {current_total_achieved_mbps:.2f} Mbps (< {saturation_factor * 100:.0f}% of aggregate {attempted_aggregate_target_bw:.2f} Mbps).")

                    if udp_bw_frozen:
                        udp_bw_for_stream_push_phase = current_udp_bw_mbps
                        if not new_overall_max_found_this_iter: udp_no_gain_stream_phase_count += 1
                        if self.verbose_logging and not new_overall_max_found_this_iter: self._log(
                            f"[V] UDP Iter {iteration_num}: Stream push at {udp_bw_for_stream_push_phase}M/stream: No new max. Count: {udp_no_gain_stream_phase_count}/{self.config['udp_no_gain_threshold_after_bw_freeze']}.")

                        if udp_no_gain_stream_phase_count >= self.config["udp_no_gain_threshold_after_bw_freeze"]:
                            self._log(
                                f"[*] UDP Iter {iteration_num}: Concluding stream push. No new overall max after {udp_no_gain_stream_phase_count} checks. Best: {self.max_achieved_tp_overall:.2f} Mbps at {self.max_achieved_tp_udp_lossless_bw}M/stream (target BW {udp_bw_for_stream_push_phase}M/stream).")
                            self._conclude_test_and_summarize(
                                f"UDP: Stream push no new max (BW {udp_bw_for_stream_push_phase}M/stream).");
                            return

                        next_streams_to_try = current_streams + udp_stream_step
                        reverted_from_streams = self.last_reverted_lossy_info.get(udp_bw_for_stream_push_phase)

                        if reverted_from_streams is not None and next_streams_to_try == reverted_from_streams:
                            self._log(
                                f"[*] UDP Iter {iteration_num}: Avoiding re-test of {next_streams_to_try} streams (recently failed by loss at {udp_bw_for_stream_push_phase}M BW). Concluding stream push.")
                            self._conclude_test_and_summarize(
                                f"UDP: Stream push avoided lossy re-test {udp_bw_for_stream_push_phase}M/{next_streams_to_try}s");
                            return
                        else:
                            current_streams = next_streams_to_try
                            if udp_bw_for_stream_push_phase in self.last_reverted_lossy_info:
                                del self.last_reverted_lossy_info[udp_bw_for_stream_push_phase]

                    elif udp_coarse_search_phase:
                        if udp_point_saturated_this_iteration:
                            self._log(
                                f"[*] UDP Coarse Search: Saturation at {current_udp_bw_mbps}M/stream. Transitioning to Binary Fine-Tuning.")
                            udp_coarse_search_phase = False;
                            udp_binary_fine_tuning_phase = True
                            udp_bs_high_bound = current_udp_bw_mbps
                        else:
                            if self.verbose_logging: self._log(
                                f"[*] UDP Coarse Search: Success at {current_udp_bw_mbps}M/stream (not saturated).")
                            udp_bs_low_bound = current_udp_bw_mbps
                            current_udp_bw_mbps += udp_bw_coarse_step

                    if udp_binary_fine_tuning_phase:  # This block can be entered from coarse search or if already in binary search
                        if udp_point_saturated_this_iteration and not udp_coarse_search_phase:  # Current point passed loss but is saturated
                            udp_bs_high_bound = current_udp_bw_mbps
                            if self.verbose_logging: self._log(
                                f"[*] UDP Binary Fine-Tuning: Step at {current_udp_bw_mbps}M/stream passed loss but IS SATURATED. Updating high bound.")
                        elif not udp_point_saturated_this_iteration:  # Good binary step (passed loss, not saturated)
                            udp_bs_low_bound = current_udp_bw_mbps
                            if self.verbose_logging: self._log(
                                f"[*] UDP Binary Fine-Tuning: Good step at {current_udp_bw_mbps}M/stream (stable, not saturated).")

                        if (int(udp_bs_high_bound) - udp_bs_low_bound) <= 1 and udp_bs_high_bound != float('inf'):
                            settle_bw = self.max_achieved_tp_udp_lossless_bw
                            if settle_bw == 0 and udp_last_lossless_target_bw > 0: settle_bw = udp_last_lossless_target_bw
                            if settle_bw == 0: settle_bw = udp_bs_low_bound
                            if settle_bw == 0 and min_practical_bw > 0: settle_bw = min_practical_bw
                            if settle_bw <= 0: settle_bw = min_practical_bw

                            self._log(f"[*] UDP Binary Fine-Tuning converged. Settling at {settle_bw}M/stream.")
                            current_udp_bw_mbps = settle_bw;
                            udp_bw_frozen = True;
                            udp_binary_fine_tuning_phase = False;
                            udp_coarse_search_phase = False
                            udp_bw_for_stream_push_phase = current_udp_bw_mbps;
                            udp_no_gain_stream_phase_count = 0
                            if udp_bw_for_stream_push_phase in self.last_reverted_lossy_info:
                                del self.last_reverted_lossy_info[udp_bw_for_stream_push_phase]
                            self._log(
                                f"[*] UDP Bandwidth frozen at {current_udp_bw_mbps}M/stream. Starting stream pushing.")
                        else:
                            current_udp_bw_mbps = int(round((udp_bs_low_bound + udp_bs_high_bound) / 2.0))
                            if current_udp_bw_mbps <= udp_bs_low_bound and udp_bs_high_bound != float('inf'):
                                current_udp_bw_mbps = udp_bs_low_bound + 1
                            elif current_udp_bw_mbps <= udp_bs_low_bound:
                                current_udp_bw_mbps = udp_bs_low_bound
                            if udp_bs_high_bound != float('inf') and current_udp_bw_mbps >= int(
                                udp_bs_high_bound): current_udp_bw_mbps = int(udp_bs_high_bound) - 1
                            if current_udp_bw_mbps <= 0: current_udp_bw_mbps = min_practical_bw
                            high_bound_str = str(int(udp_bs_high_bound)) if udp_bs_high_bound != float('inf') else 'Inf'
                            if self.verbose_logging: self._log(
                                f"[*] UDP Binary Fine-Tuning: Next BW to test {current_udp_bw_mbps}M/stream (Low: {udp_bs_low_bound}M, High: {high_bound_str}M).")

                else:  # Loss > limit OR confirmation failed (is_stable_and_confirmed_pass is False)
                    if current_test_initial_pass and not is_stable_and_confirmed_pass:  # This means it failed a confirmation run
                        udp_loss_retry_count = self.config[
                                                   'udp_max_loss_retry_attempts'] + 1  # Force into retry logic below
                        if self.verbose_logging: self._log(
                            f"[!] UDP Iter {iteration_num}: {current_udp_bw_mbps}M/stream failed confirmation (Reason: {'Loss' if current_loss_percent <= 100.0 else 'Exec Error'}).")
                    elif not current_test_initial_pass:  # Failed the initial test
                        udp_loss_retry_count += 1
                        if self.verbose_logging: self._log(
                            f"[!] UDP Iter {iteration_num}: Loss {current_loss_percent:.2f}% > {self.loss_limit:.2f}% at {current_udp_bw_mbps}M/stream. Loss retry count {udp_loss_retry_count}/{self.config['udp_max_loss_retry_attempts']}.")

                    if udp_loss_retry_count > self.config['udp_max_loss_retry_attempts']:
                        udp_loss_retry_count = 0
                        failing_bw = current_udp_bw_mbps

                        if udp_bw_frozen:
                            self._log(
                                f"[!] UDP Iter {iteration_num}: Persistent loss ({current_loss_percent:.2f}%) at frozen BW {udp_bw_for_stream_push_phase}M/stream with {current_streams} streams.")
                            attempted_streams_that_failed = current_streams
                            self.last_reverted_lossy_info[udp_bw_for_stream_push_phase] = attempted_streams_that_failed
                            current_streams = max(self.config["udp_initial_streams"],
                                                  attempted_streams_that_failed - udp_stream_step)
                            if attempted_streams_that_failed == current_streams:
                                self._log(
                                    f"[!] UDP Iter {iteration_num}: Loss at frozen BW {udp_bw_for_stream_push_phase}M/stream with {current_streams} streams (min/no change possible). Concluding.")
                                self._conclude_test_and_summarize(
                                    f"UDP loss at frozen BW {udp_bw_for_stream_push_phase}M/stream, stream count {current_streams} unstable.");
                                return
                            self._log(
                                f"[*] UDP Iter {iteration_num}: Reverted streams from {attempted_streams_that_failed} to {current_streams} for BW {udp_bw_for_stream_push_phase}M/stream.")
                            udp_no_gain_stream_phase_count = self.config["udp_no_gain_threshold_after_bw_freeze"] - 1
                        elif udp_coarse_search_phase:
                            self._log(
                                f"[*] UDP Coarse Search: Failed at {failing_bw}M/stream (due to loss). Last known good target BW was {udp_last_lossless_target_bw}M/stream. Transitioning to Binary.")
                            udp_coarse_search_phase = False;
                            udp_binary_fine_tuning_phase = True
                            udp_bs_high_bound = failing_bw
                        elif udp_binary_fine_tuning_phase:
                            self._log(f"[*] UDP Binary Fine-Tuning: Failed at {failing_bw}M/stream (due to loss).")
                            udp_bs_high_bound = failing_bw

                        if udp_binary_fine_tuning_phase:  # This block can be entered from coarse search or if already in binary search after a loss
                            if int(udp_bs_high_bound) <= udp_bs_low_bound:
                                settle_bw = self.max_achieved_tp_udp_lossless_bw
                                if settle_bw == 0 and udp_last_lossless_target_bw > 0: settle_bw = udp_last_lossless_target_bw
                                if settle_bw == 0: settle_bw = udp_bs_low_bound
                                if settle_bw == 0 and min_practical_bw > 0: settle_bw = min_practical_bw
                                if settle_bw <= 0: settle_bw = min_practical_bw

                                self._log(
                                    f"[*] UDP Binary Fine-Tuning (or transition) bounds invalid/converged ({udp_bs_low_bound}M >= {int(udp_bs_high_bound)}M after loss). Settling at {settle_bw}M/stream.")
                                current_udp_bw_mbps = settle_bw;
                                udp_bw_frozen = True;
                                udp_binary_fine_tuning_phase = False;
                                udp_coarse_search_phase = False
                                udp_bw_for_stream_push_phase = current_udp_bw_mbps;
                                udp_no_gain_stream_phase_count = 0
                                if udp_bw_for_stream_push_phase in self.last_reverted_lossy_info:
                                    del self.last_reverted_lossy_info[udp_bw_for_stream_push_phase]
                                self._log(
                                    f"[*] UDP Bandwidth frozen at {current_udp_bw_mbps}M/stream. Starting stream pushing.")
                            else:
                                current_udp_bw_mbps = int(round((udp_bs_low_bound + udp_bs_high_bound) / 2.0))
                                if current_udp_bw_mbps <= udp_bs_low_bound and udp_bs_high_bound != float('inf'):
                                    current_udp_bw_mbps = udp_bs_low_bound + 1
                                elif current_udp_bw_mbps <= udp_bs_low_bound:
                                    current_udp_bw_mbps = udp_bs_low_bound
                                if udp_bs_high_bound != float('inf') and current_udp_bw_mbps >= int(
                                    udp_bs_high_bound): current_udp_bw_mbps = int(udp_bs_high_bound) - 1
                                if current_udp_bw_mbps <= 0: current_udp_bw_mbps = min_practical_bw
                                high_bound_str = str(int(udp_bs_high_bound)) if udp_bs_high_bound != float(
                                    'inf') else 'Inf'
                                if self.verbose_logging: self._log(
                                    f"[*] UDP Binary Fine-Tuning: Next BW to test {current_udp_bw_mbps}M/stream (Low: {udp_bs_low_bound}M, High: {high_bound_str}M).")

                if not udp_bw_frozen and current_udp_bw_mbps <= 0:
                    recovered_bw = udp_last_lossless_target_bw if udp_last_lossless_target_bw > 0 else min_practical_bw
                    if recovered_bw <= 0: recovered_bw = self.config.get("udp_min_bw_for_binary_search_low_bound_mbps",
                                                                         1)
                    self._log(
                        f"[!] UDP BW target became non-positive ({current_udp_bw_mbps}M/stream). Reverting to {recovered_bw}M/stream and freezing.")
                    current_udp_bw_mbps = recovered_bw
                    udp_bw_frozen = True;
                    udp_binary_fine_tuning_phase = False;
                    udp_coarse_search_phase = False
                    udp_bw_for_stream_push_phase = current_udp_bw_mbps;
                    udp_no_gain_stream_phase_count = 0
                    if udp_bw_for_stream_push_phase in self.last_reverted_lossy_info:
                        del self.last_reverted_lossy_info[udp_bw_for_stream_push_phase]
                    self._log(f"[*] UDP Bandwidth frozen at {current_udp_bw_mbps}M/stream. Starting stream pushing.")
                    if current_udp_bw_mbps <= 0:
                        self._conclude_test_and_summarize("UDP BW error (non-positive target after recovery)");
                        return

                if current_udp_bw_mbps > 20000 or current_streams > 64:  # Safety limits
                    self._conclude_test_and_summarize(
                        f"UDP params (BW {current_udp_bw_mbps}M/stream or Streams {current_streams}) exceeded safety limits.");
                    return

            else:  # TCP Logic
                is_valid_data_for_calc = (current_total_achieved_mbps > 0.001)
                # Check if current_total_achieved_mbps is not from an interrupted/stopped test before updating max
                if not is_interrupt_error_from_exec and is_valid_data_for_calc:
                    if current_total_achieved_mbps > self.max_achieved_tp_overall:
                        self.max_achieved_tp_overall, self.max_achieved_tp_tx, self.max_achieved_tp_rx = current_total_achieved_mbps, current_tx_mbps, current_rx_mbps
                        self.max_achieved_tp_streams = current_streams
                        tcp_no_gain_count = 0;
                        self.tcp_throughput_history.clear()
                        if self.verbose_logging: self._log(
                            f"[*] New max TCP throughput: {self.max_achieved_tp_overall:.2f} Mbps at {current_streams} streams{log_msg_detail} (Iter {iteration_num})")
                    else:
                        if current_streams > self.config[
                            "tcp_initial_streams"] or self.max_achieved_tp_overall > 1: tcp_no_gain_count += 1
                        if self.verbose_logging: self._log(
                            f"[*] TCP Iter {iteration_num}: No improvement vs overall max ({self.max_achieved_tp_overall:.2f} Mbps), no_gain_count {tcp_no_gain_count}/{self.config['tcp_no_gain_threshold']}.")

                    self.tcp_throughput_history.append(current_total_achieved_mbps)
                    if len(self.tcp_throughput_history) >= self.config["tcp_plateau_iterations"]:
                        if iteration_num >= (self.config["tcp_initial_streams"] + self.config[
                            "tcp_plateau_iterations"] - 1):  # Ensure enough data points
                            min_hist_tp, max_hist_tp = min(self.tcp_throughput_history), max(
                                self.tcp_throughput_history)
                            ref_hist_tp = sum(self.tcp_throughput_history) / len(
                                self.tcp_throughput_history) if self.tcp_throughput_history else 0.1
                            if ref_hist_tp > 0.1:  # Avoid division by zero or tiny numbers
                                percentage_diff = ((max_hist_tp - min_hist_tp) / ref_hist_tp) * 100
                                if self.verbose_logging: self._log(
                                    f"[*] TCP Plateau Check: History=[{', '.join([f'{x:.2f}' for x in self.tcp_throughput_history])}], Diff={percentage_diff:.2f}%")
                                if percentage_diff < self.config["tcp_plateau_percentage_variance"]:
                                    self._log(
                                        f"[*] TCP throughput plateau detected: Last {self.config['tcp_plateau_iterations']} iterations within {self.config['tcp_plateau_percentage_variance']:.2f}% variance.")
                                    self._conclude_test_and_summarize(
                                        f"TCP throughput plateaued (iter {iteration_num}).");
                                    return
                elif is_interrupt_error_from_exec and self.verbose_logging:
                    self._log(
                        f"[*] Iter {iteration_num} (TCP): Interrupted, TP ({current_total_achieved_mbps:.2f} Mbps) not for max/plateau.")
                elif not is_interrupt_error_from_exec and self.verbose_logging:  # and not is_valid_data_for_calc
                    self._log(
                        f"[*] TCP Iter {iteration_num}: No valid TP data (Total: {current_total_achieved_mbps:.2f} Mbps) for max/plateau.")

                if tcp_no_gain_count >= self.config["tcp_no_gain_threshold"]:
                    self._conclude_test_and_summarize(
                        f"TCP no improvement after {tcp_no_gain_count} attempts (iter {iteration_num}).");
                    return

                current_streams += self.config["tcp_streams_increment"]
                if current_streams > 128:  # Safety limit
                    self._conclude_test_and_summarize(
                        f"TCP stream safety limit ({current_streams}) reached (iter {iteration_num}).");
                    return

            if self.stop_flag:
                if self.verbose_logging: self._log(
                    f"[*] Iter {iteration_num}: Stop flag detected, breaking main loop after processing.")
                break
            time.sleep(self.config["main_loop_settle_s"])

        if not self.concluded_event.is_set():  # Ensure conclusion if loop finishes naturally or by break without explicit conclude
            reason = "Test stopped by user or flag" if self.stop_flag else "Test iterations naturally completed"
            self._conclude_test_and_summarize(reason)

    def _start_latency_monitor(self):  # Same as previous version
        def monitor():
            pattern = re.compile(r"time=([\d.]+)\s*ms", re.IGNORECASE)
            avg_pattern = re.compile(r"(?:min/avg/max|rtt min/avg/max)(?:/[^=]+)?\s*=\s*[\d.]+/([\d.]+)/",
                                     re.IGNORECASE)
            while not self.latency_stop_flag and not self.stop_flag:
                latency_value = 9999.0
                try:
                    cmd = ["ping", "-c", str(self.config["latency_ping_count"]), "-W",
                           str(self.config["latency_ping_packet_timeout_s"]), self.remote_ip]
                    process_timeout = (self.config["latency_ping_packet_timeout_s"] * self.config[
                        "latency_ping_count"]) + 2
                    ping_proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                               timeout=process_timeout, check=False)
                    output = ping_proc.stdout
                    avg_match = avg_pattern.search(output)
                    if avg_match:
                        latency_value = float(avg_match.group(1))
                    else:
                        matches = pattern.findall(output)
                        if matches:
                            latency_value = float(matches[-1])
                        elif "0% packet loss" not in output and (
                                "100% packet loss" in output or ping_proc.returncode != 0):
                            if self.verbose_logging: self._log("[V][Latency] Ping timeout or 100% loss.")
                        elif "0% packet loss" in output and not matches and not avg_match:  # Successful ping but no RTT parsed (e.g. some systems)
                            if self.verbose_logging: self._log(
                                "[V][Latency] Ping OK but no RTT parsed. Assuming near-zero or check ping output format.")
                            latency_value = 0.0  # Or handle as error if 0.0 is misleading
                except subprocess.TimeoutExpired:
                    if self.verbose_logging: self._log("[V][Latency] Ping command timed out.")
                except FileNotFoundError:
                    self._log("[!] Ping command not found. Latency monitoring stopping, records 0.")
                    self.latency_stop_flag = True;
                    latency_value = 0.0  # Stop latency monitor if ping not found
                except Exception as e:
                    if self.verbose_logging: self._log(f"[V][Latency] Error: {e}")
                self.data["latency"].append(latency_value)
                if self.config["latency_ping_interval_s"] > 0 and not self.latency_stop_flag and not self.stop_flag:
                    # Accurate sleep considering processing time
                    sleep_duration = self.config["latency_ping_interval_s"]
                    # Can add logic here to make sleep more precise if needed, but time.sleep is usually sufficient
                    time.sleep(sleep_duration)
            if self.verbose_logging: self._log("[*] Latency monitoring thread exiting.")

        self.latency_thread = threading.Thread(target=monitor, daemon=True)
        self.latency_thread.start()

    def _conclude_test_and_summarize(self, reason):  # Same as previous version
        if self.concluded_event.is_set(): return
        self.concluded_event.set()
        current_thread_name = threading.current_thread().name
        if self.verbose_logging: self._log(f"[*] Concluding test from thread: {current_thread_name}. Reason: {reason}")

        self.stop_flag = True  # Ensure stop_flag is set
        self.latency_stop_flag = True
        if self.latency_thread and self.latency_thread.is_alive() and threading.current_thread() != self.latency_thread:
            if self.verbose_logging: self._log("[*] Waiting for latency thread to join...")
            try:
                # Calculate a reasonable join timeout based on ping configuration
                join_timeout = self.config.get("latency_ping_packet_timeout_s", 1) * self.config.get(
                    "latency_ping_count", 1) + self.config.get("latency_ping_interval_s", 1) + 2  # Add buffer
                self.latency_thread.join(timeout=max(1, join_timeout))  # Ensure at least 1s timeout
                if self.latency_thread.is_alive() and self.verbose_logging: self._log(
                    "[Warning] Latency thread join timed out.")
            except Exception as e:
                if self.verbose_logging: self._log(f"[Warning] Error joining latency thread: {e}")

        self._log(f"\n-------- iPerf3 Test Concluded ({reason}) --------")  # Original log line moved after join
        self._final_summary_box()
        if self.verbose_logging: self._log("[*] Performing final iPerf process cleanup.")
        kill_iperf()  # Call kill_iperf after summary

        if self.ui:  # UI updates
            try:
                if self.ui.get('start_button'): self.ui['start_button'].config(state="normal")
                if self.ui.get('stop_button'): self.ui['stop_button'].config(state="disabled")
                if self.ui.get('status_bar'): self.ui['status_bar'].config(text="Test Stopped/Completed")
                for btn_key in ['export_log_btn', 'save_tp_graph_btn', 'save_latency_graph_btn']:
                    if btn_key in self.ui and self.ui.get(btn_key):
                        can_enable = False
                        if 'log' in btn_key and self.ui.get('output_area'):
                            try:  # Check if output area has content
                                if self.ui['output_area'].index("end-1c") != "1.0": can_enable = True
                            except:
                                pass  # Handle cases where widget might not be fully initialized
                        elif 'graph' in btn_key and self.data['timestamp']:  # Check if there's data to graph
                            can_enable = True
                        self.ui[btn_key].config(state="normal" if can_enable else "disabled")
            except Exception as e:
                if self.verbose_logging: self._log(f"[UI Error] Failed to update UI elements post-test: {e}")

    def _final_summary_box(self):  # Same as previous version
        duration = int(time.time() - self.start_time) if self.start_time > 0 else 0
        mins, secs = divmod(duration, 60)
        box_width, content_width, key_text_max_width, separator = 70, 66, 30, " : "
        summary_lines = ["\n" + f"+{'-' * (box_width - 2)}+"]

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
        summary_lines.extend([f"| {title.center(content_width)} |", f"+{'-' * (box_width - 2)}+"])
        add_line_to_summary("Test Duration", f"{mins:02}:{secs:02} (mm:ss)")
        add_line_to_summary("Profile", self.profile)
        add_line_to_summary("Packet Size", f"{self.packet_size} bytes" if self.packet_size else "Default")
        if self.protocol == "UDP": add_line_to_summary("UDP Loss Threshold Set", f"{self.loss_limit:.2f}%")
        add_line_to_summary("", "")
        add_line_to_summary("--- Max Achieved Stable Throughput ---")
        tp_label_suffix = ""
        if self.protocol == "UDP":
            tp_label_suffix = " (Est. Server Received)" if self.direction == "Uplink" else " (Est. Client Received)" if self.direction == "Downlink" else ""

        add_line_to_summary("Total Achieved", f"{self.max_achieved_tp_overall:.2f} Mbps{tp_label_suffix}")
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
            all_recorded_latencies = [l for l in self.data['latency'] if l is not None]  # Includes 9999.0 for timeouts
            avg_lat, min_lat, max_lat = 0.0, 0.0, 0.0
            ping_timeout_display_val = self.config['latency_ping_packet_timeout_s'] * 1000
            timeout_occurred = any(l == 9999.0 for l in all_recorded_latencies)

            if valid_latencies:
                avg_lat = sum(valid_latencies) / len(valid_latencies)
                min_lat = min(valid_latencies);
                max_lat = max(valid_latencies)

            if not valid_latencies and timeout_occurred:  # All pings timed out
                avg_lat_str, min_lat_str, max_lat_str = "N/A (Pings Failed)", "N/A", f"> {ping_timeout_display_val:.0f} ms (timeout)"
            elif not valid_latencies and not timeout_occurred:  # No pings, no timeouts (e.g., monitor stopped before first ping)
                avg_lat_str, min_lat_str, max_lat_str = "N/A", "N/A", "N/A"
            else:  # Some valid latencies
                avg_lat_str, min_lat_str = f"{avg_lat:.2f} ms", f"{min_lat:.2f} ms";
                max_lat_str = f"{max_lat:.2f} ms"
                if timeout_occurred: max_lat_str += f" (some >{ping_timeout_display_val:.0f}ms)"
            add_line_to_summary("", "");
            add_line_to_summary("--- Latency (Ping based) ---")
            add_line_to_summary("  Average ", avg_lat_str);
            add_line_to_summary("  Min ", min_lat_str);
            add_line_to_summary("  Max ", max_lat_str)

        local_tx_data = self.data.get('local_tx', [])  # Use .get() for safety
        local_rx_data = self.data.get('local_rx', [])  # Use .get() for safety
        throughput_data = self.data.get('throughput', [])  # Use .get() for safety

        if self.verbose_logging and local_tx_data:  # Check if local_tx_data list is not empty
            add_line_to_summary("", "");
            add_line_to_summary("--- Last Measured Live Data (Verbose) ---")
            add_line_to_summary("  Local Tx", f"{local_tx_data[-1]:.2f} Mbps");  # Use local_tx_data
            if local_rx_data:  # Check if local_rx_data is not empty
                add_line_to_summary("  Local Rx", f"{local_rx_data[-1]:.2f} Mbps")  # Use local_rx_data
            else:
                add_line_to_summary("  Local Rx", "N/A")
            if throughput_data:  # Check if throughput_data is not empty
                add_line_to_summary("  Total Achieved", f"{throughput_data[-1]:.2f} Mbps")
            else:
                add_line_to_summary("  Total Achieved", "N/A")
        summary_lines.append(f"+{'-' * (box_width - 2)}+")
        for line in summary_lines: self._log(line)

    def _log(self, msg):  # Same as previous version
        if self.ui and self.ui.get('output_area'):
            try:
                output_area = self.ui['output_area']
                if output_area.winfo_exists():  # Check if widget still exists
                    output_area.config(state='normal')
                    output_area.insert("end", msg + "\n")
                    output_area.config(state='disabled')
                    output_area.see("end")
            except Exception:  # Fallback if UI logging fails
                print(msg)
        else:  # No UI, print to console
            print(msg)