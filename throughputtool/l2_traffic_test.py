# l2_traffic_test.py

import subprocess
import threading
import time
import psutil
import importlib.resources
import os
import signal  # Added for signal constants
import paramiko  # For SSH functionality
import traceback
import tkinter as tk
import re  # For parsing ping output
import statistics  # For mean

# --- Constants ---
REMOTE_SCRIPT_NAME = "remote_rx_agent.py"
REMOTE_PYTHON_EXEC = "python3"


class L2TrafficTest:
    def __init__(self, iface, ui_refs, ethertype="All", remote_mac="", packet_size=1400,
                 target_l2_rate=50.0,
                 profile="Moderate",  # Currently unused in L2 test
                 data_store=None, graph=None, update_metrics=None,
                 remote_ip="", remote_user="", remote_pass="", remote_iface_name="",
                 measure_remote_rx=False, verbose=False):

        self.iface = iface
        self.ui = ui_refs
        self.ethertype_str = ethertype
        self.remote_mac = remote_mac.strip()
        self.packet_size = int(packet_size)
        self.target_l2_rate = float(target_l2_rate)
        self.profile = profile
        self.verbose = verbose
        self.measure_remote_rx = measure_remote_rx

        self.data = data_store if data_store is not None else {}
        for key_to_ensure in ["local_tx", "local_rx", "latency", "timestamp", "throughput"]:
            self.data.setdefault(key_to_ensure, [])
        if self.measure_remote_rx:
            self.data.setdefault("remote_rx", [])

        self.graph = graph
        self.update_metrics_callback = update_metrics

        ethertype_map = {
            "IPv4": "0x0800", "ARP": "0x0806", "IPv6": "0x86DD",
            "VLAN": "0x8100", "MPLS": "0x8847", "PPPoE": "0x8864",
            "Loopback": "0x9000", "Unknown": "0x88B5", "0xFFFF": "0xFFFF"
        }
        self.ethertype_code = ethertype_map.get(self.ethertype_str, "0x0800")

        self.l2_flooder_packets_sent = None
        self.high_load_warning_logged = False

        self.remote_ip = remote_ip
        self.remote_user = remote_user
        self.remote_pass = remote_pass
        self.remote_iface_name = remote_iface_name
        self.ssh_client = None
        self.ssh_channel = None
        self.ssh_stdout_thread = None
        self.ssh_stderr_thread = None
        self.remote_rx_pid = None

        self.stop_flag = False
        self.process = None
        self.thread = None

        self.latency_thread = None
        self.latency_stop_flag = False
        self.latency_config = {
            "ping_interval_s": 1,
            "ping_packet_timeout_s": 4,
            "ping_count": 1,
            "ping_process_timeout_margin_s": 2,
            "ping_timeout_placeholder_ms": 9999.0
        }
        self.ping_rtt_pattern = re.compile(r"time=([\d.]+)\s*ms", re.IGNORECASE)
        self.ping_avg_rtt_pattern = re.compile(r"(?:min/avg/max|rtt min/avg/max)(?:/[^=]+)?\s*=\s*[\d.]+/([\d.]+)/",
                                               re.IGNORECASE)

        self.initial_skip_seconds = 3

    def _log(self, msg, is_verbose=False):
        if not is_verbose or self.verbose:
            if self.ui and self.ui.get('output_area'):
                try:
                    output_area = self.ui['output_area']
                    if output_area.winfo_exists():
                        output_area.config(state='normal')
                        output_area.insert("end", msg + "\n")
                        output_area.config(state='disabled')
                        output_area.see("end")
                    else:
                        if self.verbose or not is_verbose: print(f"(UI Widget Gone) {msg}")
                except Exception as e:
                    if self.verbose or not is_verbose: print(f"(UI Log Error: {e}) {msg}")
            else:
                if self.verbose or not is_verbose: print(msg)

    def run(self):
        self.stop_flag = False
        self.latency_stop_flag = False
        self.high_load_warning_logged = False

        for key in ["local_tx", "local_rx", "latency", "timestamp", "throughput"]:
            self.data.setdefault(key, []).clear()
        if self.measure_remote_rx:
            self.data.setdefault("remote_rx", []).clear()

        if self.thread and self.thread.is_alive():
            self._log("[Warning] Test thread is already running.", is_verbose=True);
            return

        self.thread = threading.Thread(target=self._run_test, daemon=True)
        self.thread.start()
        if self.ui and self.ui.get('start_button'): self.ui['start_button'].config(state="disabled")
        if self.ui and self.ui.get('stop_button'): self.ui['stop_button'].config(state="normal")
        if self.ui and self.ui.get('status_bar'): self.ui['status_bar'].config(text="Test Running...")

    def stop(self):
        self._log("[*] Stop command received. Attempting to stop L2/L3 Traffic Test...")
        self.stop_flag = True
        self.latency_stop_flag = True
        if self.ui and self.ui.get('status_bar'): self.ui['status_bar'].config(text="Test Stopping...")

    def _start_latency_monitor(self):
        if not self.remote_ip:
            self._log("[Info] No remote IP specified, skipping latency monitoring.", is_verbose=True)
            return False

        if self.latency_thread and self.latency_thread.is_alive():
            self._log("[Warning] Latency monitor thread already running.", is_verbose=True)
            return True

        self.latency_stop_flag = False
        self.latency_thread = threading.Thread(target=self._monitor_latency, daemon=True)
        self.latency_thread.start()
        self._log("[*] Latency monitoring thread started.", is_verbose=True)
        return True

    def _monitor_latency(self):
        self._log("[Info] Latency monitor loop started.", is_verbose=True)
        while not self.latency_stop_flag and not self.stop_flag:
            latency_value = self.latency_config["ping_timeout_placeholder_ms"]
            try:
                cmd = ["ping",
                       "-c", str(self.latency_config["ping_count"]),
                       "-W", str(self.latency_config["ping_packet_timeout_s"]),
                       self.remote_ip]

                process_timeout = (self.latency_config["ping_packet_timeout_s"] * self.latency_config["ping_count"]) + \
                                  self.latency_config["ping_process_timeout_margin_s"]

                if self.verbose: self._log(f"[LatencyMon] Executing: {' '.join(cmd)}", is_verbose=True)

                ping_proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                           text=True, timeout=process_timeout, check=False)
                output = ping_proc.stdout
                if self.verbose: self._log(f"[LatencyMon] Ping output:\n{output}", is_verbose=True)

                avg_match = self.ping_avg_rtt_pattern.search(output)
                if avg_match:
                    latency_value = float(avg_match.group(1))
                else:
                    matches = self.ping_rtt_pattern.findall(output)
                    if matches:
                        latency_value = float(matches[-1])

            except subprocess.TimeoutExpired:
                if self.verbose: self._log("[LatencyMon] Ping command timed out.", is_verbose=True)
            except FileNotFoundError:
                self._log("[ERROR] Ping command not found. Latency monitoring stopping.", is_verbose=False)
                self.latency_stop_flag = True
            except Exception as e:
                if self.verbose: self._log(f"[LatencyMon] Error: {e}", is_verbose=True)

            self.data.setdefault("latency", []).append(latency_value)

            if self.latency_config["ping_interval_s"] > 0 and \
                    not self.latency_stop_flag and \
                    not self.stop_flag:
                time.sleep(self.latency_config["ping_interval_s"])

        self._log("[Info] Latency monitor loop finished.", is_verbose=True)

    def _connect_ssh(self):
        if not self.measure_remote_rx: return True
        self._log(f"[*] Attempting SSH connection to {self.remote_user}@{self.remote_ip}...", is_verbose=True)
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.ssh_client.connect(self.remote_ip, username=self.remote_user, password=self.remote_pass, timeout=10)
            self._log("[+] SSH Connected successfully.")
            return True
        except paramiko.AuthenticationException:
            self._log("[ERROR] SSH Authentication failed. Please check username/password.")
            return False
        except Exception as e:
            self._log(f"[ERROR] SSH connection to {self.remote_ip} failed: {e} (Type: {type(e).__name__})")
            return False

    def _log_ssh_stderr(self, remote_stderr_pipe):
        try:
            for line in iter(remote_stderr_pipe.readline, ""):
                line = line.strip()
                if not line: continue
                self._log(f"[REMOTE_AGENT_STDERR] {line}")
        except Exception as e:
            self._log(f"[ERROR] Exception while reading remote agent stderr: {e}", is_verbose=True)
        finally:
            if remote_stderr_pipe: remote_stderr_pipe.close()

    def _deploy_and_run_remote_agent(self, interval=1.0):
        local_script_path = ""
        script_found_method = ""
        try:
            package_name_for_resource = __package__ if __package__ else os.path.basename(
                os.path.dirname(os.path.abspath(__file__)))
            if not package_name_for_resource: package_name_for_resource = 'throughputtool'

            with importlib.resources.path(package_name_for_resource, REMOTE_SCRIPT_NAME) as script_file_path_obj:
                local_script_path = str(script_file_path_obj)
                script_found_method = "package resource"
        except Exception as e_pkg_res:
            self._log(
                f"[Warning] Could not find '{REMOTE_SCRIPT_NAME}' via package resources ({e_pkg_res}). Trying relative paths.",
                is_verbose=True)
            try:
                current_module_dir = os.path.dirname(os.path.abspath(__file__))
                script_in_module_dir = os.path.join(current_module_dir, REMOTE_SCRIPT_NAME)
                if os.path.exists(script_in_module_dir):
                    local_script_path = script_in_module_dir
                    script_found_method = "module directory"
                else:
                    script_in_cwd = os.path.join(os.getcwd(), REMOTE_SCRIPT_NAME)
                    if os.path.exists(script_in_cwd):
                        local_script_path = script_in_cwd
                        script_found_method = "current working directory"
                    else:
                        self._log(
                            f"[ERROR] '{REMOTE_SCRIPT_NAME}' not found via package resources, module directory ('{script_in_module_dir}'), or CWD ('{script_in_cwd}').")
                        return False
            except NameError:
                local_script_path = os.path.join(os.getcwd(), REMOTE_SCRIPT_NAME)
                if os.path.exists(local_script_path):
                    script_found_method = "current working directory (__file__ missing)"
                else:
                    self._log(f"[ERROR] '{REMOTE_SCRIPT_NAME}' not found in CWD (__file__ undefined).");
                    return False

        self._log(
            f"[*] Using local path for remote agent script (found via {script_found_method}): {local_script_path}",
            is_verbose=True)
        if not os.path.exists(local_script_path): self._log(
            f"[ERROR] Path for '{REMOTE_SCRIPT_NAME}' ('{local_script_path}') does not exist."); return False

        remote_tmp_path = f"/tmp/{REMOTE_SCRIPT_NAME}"
        try:
            self._log(f"[*] Copying '{local_script_path}' to {self.remote_ip}:{remote_tmp_path} via SFTP...",
                      is_verbose=True)
            sftp = self.ssh_client.open_sftp()
            sftp.put(local_script_path, remote_tmp_path)
            sftp.chmod(remote_tmp_path, 0o755)
            sftp.close()
            self._log("[+] Remote agent script copied successfully.", is_verbose=True)

            cmd_remote = f"{REMOTE_PYTHON_EXEC} {remote_tmp_path} {self.remote_iface_name} {interval}"
            self._log(f"[*] Executing remote agent: {cmd_remote}", is_verbose=True)

            stdin, stdout, stderr = self.ssh_client.exec_command(cmd_remote, bufsize=1, get_pty=False)
            self.ssh_channel = stdout.channel

            self.ssh_stdout_thread = threading.Thread(target=self._parse_remote_rx_output, args=(stdout,), daemon=True)
            self.ssh_stderr_thread = threading.Thread(target=self._log_ssh_stderr, args=(stderr,), daemon=True)
            self.ssh_stdout_thread.start()
            self.ssh_stderr_thread.start()

            time.sleep(1.5)
            if self.ssh_channel and self.ssh_channel.exit_status_ready():
                self._log(
                    f"[Warning] Remote agent may have exited prematurely. Status: {self.ssh_channel.recv_exit_status()}. Check REMOTE_AGENT_STDERR logs.",
                    is_verbose=True)
            return True
        except Exception as e:
            self._log(f"[ERROR] Failed to deploy/run remote agent: {e} (Type: {type(e).__name__})")
            self._log(traceback.format_exc(), is_verbose=True)
            return False

    def _parse_remote_rx_output(self, remote_stdout_pipe):
        self._log("[INFO] _parse_remote_rx_output thread started.", is_verbose=True)
        lines_processed = 0
        try:
            for line in iter(remote_stdout_pipe.readline, ""):
                lines_processed += 1
                line = line.strip()
                if not line: continue

                if self.verbose: self._log(f"[REMOTE_AGENT_RAW] {line}", is_verbose=True)
                if line.startswith("DEBUG_REMOTE_") and self.verbose:
                    self._log(f"[REMOTE_AGENT_DEBUG] {line}", is_verbose=True)

                if line.startswith("DATA:"):
                    try:
                        remote_rx_mbps = float(line.split(":")[1])
                        if self.verbose:
                            self._log(
                                f"[DEBUG_PARSE] Parsed DATA: {remote_rx_mbps:.2f} Mbps. Appending to self.data['remote_rx'].",
                                is_verbose=True)
                        if self.measure_remote_rx:
                            self.data.setdefault("remote_rx", []).append(remote_rx_mbps)

                        if self.ui and self.ui.get('metrics_labels') and self.ui['metrics_labels'].get('remote_rx'):
                            try:
                                self.ui['metrics_labels']['remote_rx'].config(
                                    text=f"Remote Rx: {remote_rx_mbps:.2f} Mbps")
                            except Exception as e_ui_update:
                                self._log(f"[Warning] Failed direct UI update for remote_rx: {e_ui_update}",
                                          is_verbose=True)

                    except (IndexError, ValueError) as e:
                        self._log(f"[WARNING] Could not parse remote Rx data: '{line}', Error: {e}")
                elif line.startswith("INFO:Monitoring_started_on_remote_interface"):
                    try:
                        pid_part = line.split('_pid:')[-1]
                        self.remote_rx_pid = int(pid_part)
                        self._log(f"[INFO] Remote agent started with PID: {self.remote_rx_pid}", is_verbose=True)
                    except:
                        pass
                elif line.startswith("ERROR:"):
                    self._log(f"[REMOTE_AGENT_ERROR] {line}")
            self._log(f"[INFO] _parse_remote_rx_output thread ended. Processed {lines_processed} lines.",
                      is_verbose=True)
        except Exception as e:
            self._log(f"[ERROR] Exception in _parse_remote_rx_output: {e}", is_verbose=True)
            self._log(traceback.format_exc(), is_verbose=True)
        finally:
            if remote_stdout_pipe: remote_stdout_pipe.close()

    def _stop_remote_agent(self):
        if self.ssh_client and self.measure_remote_rx:
            self._log("[*] Attempting to stop remote agent...", is_verbose=True)
            primary_stop_method_used = False
            if hasattr(self, 'ssh_channel') and self.ssh_channel and not self.ssh_channel.closed:
                self._log(
                    f"[*] Closing SSH channel for remote agent (Remote PID was: {self.remote_rx_pid if self.remote_rx_pid else 'N/A'}).",
                    is_verbose=True)
                try:
                    if not self.ssh_channel.exit_status_ready(): self.ssh_channel.send_exit_status(0)
                    self.ssh_channel.close()
                    self._log("[INFO] SSH channel for remote agent closed.", is_verbose=True)
                    primary_stop_method_used = True
                except Exception as e:
                    self._log(f"[Warning] Exception during SSH channel close: {e}", is_verbose=True)

            if not primary_stop_method_used and self.remote_rx_pid and self.ssh_client.get_transport() and self.ssh_client.get_transport().is_active():
                self._log(f"[*] Fallback: Attempting to send SIGINT to remote PID {self.remote_rx_pid}.",
                          is_verbose=True)
                try:
                    cmd_kill = f"kill -2 {self.remote_rx_pid}"
                    stdin, stdout, stderr = self.ssh_client.exec_command(cmd_kill, timeout=5)
                    if self.verbose:
                        kill_stdout = stdout.read().decode(errors='ignore').strip()
                        kill_stderr = stderr.read().decode(errors='ignore').strip()
                        exit_status = stdout.channel.recv_exit_status()
                        self._log(
                            f"[INFO] Remote kill PID {self.remote_rx_pid} status: {exit_status}, stdout: '{kill_stdout}', stderr: '{kill_stderr}'",
                            is_verbose=True)
                    self.remote_rx_pid = None
                except Exception as e:
                    self._log(f"[ERROR] Failed to send SIGINT to remote PID {self.remote_rx_pid}: {e}", is_verbose=True)

            if hasattr(self,
                       'ssh_stdout_thread') and self.ssh_stdout_thread and self.ssh_stdout_thread.is_alive(): self.ssh_stdout_thread.join(
                timeout=1.0)
            if hasattr(self,
                       'ssh_stderr_thread') and self.ssh_stderr_thread and self.ssh_stderr_thread.is_alive(): self.ssh_stderr_thread.join(
                timeout=1.0)

    def _disconnect_ssh(self):
        if self.ssh_client:
            self._log("[*] Disconnecting SSH session...", is_verbose=True)
            self.ssh_client.close()
            self.ssh_client = None
            self._log("[+] SSH Disconnected.", is_verbose=True)

    def _parse_l2_flooder_output(self, pipe):
        try:
            for line in iter(pipe.readline, ''):
                line = line.strip()
                if not line:
                    continue

                if self.verbose:
                    self._log(f"[L2_FLOODER_STDOUT] {line}", is_verbose=True)
                    continue

                if line.startswith("FINAL_STATS:PacketsSent="):
                    try:
                        count_str = line.split('=')[1]
                        self.l2_flooder_packets_sent = int(count_str)
                        self._log(
                            f"[Info] Traffic generator stopped. Total packets sent: {self.l2_flooder_packets_sent:,}")
                    except (IndexError, ValueError):
                        self._log(f"[Warning] Could not parse final packet count: {line}", is_verbose=True)

        except Exception as e:
            self._log(f"[Error] Exception in l2_flooder output parser: {e}", is_verbose=True)
        finally:
            if pipe: pipe.close()

    def _log_subprocess_output(self, pipe, pipe_name_prefix):
        try:
            for line in iter(pipe.readline, ''):
                if line: self._log(f"[{pipe_name_prefix}] {line.strip()}", is_verbose=self.verbose)
        except ValueError:
            self._log(f"[{pipe_name_prefix}] Pipe closed or error during read.", is_verbose=True)
        except Exception as e:
            self._log(f"[{pipe_name_prefix}] Error reading output: {e}", is_verbose=True)
        finally:
            if pipe: pipe.close()

    def _run_test(self):
        self.l2_flooder_packets_sent = None

        self._log("\n" + "-" * 50)
        self._log("L2/L3 Traffic Test Starting with Configuration:")
        config_details = [
            ("Interface", self.iface),
            ("Target L2 Rate", f"{self.target_l2_rate:.2f} Mbps"),
            ("Packet Size", f"{self.packet_size} bytes"),
            ("EtherType", f"{self.ethertype_str} ({self.ethertype_code})"),
            ("Destination MAC", self.remote_mac if self.remote_mac else "Broadcast (ff:ff:ff:ff:ff:ff)"),
        ]
        if self.remote_ip:
            config_details.append(("Remote Target (Ping/Agent)", self.remote_ip))

        for key, value in config_details:
            self._log(f"  {key:<25}: {value}")
        self._log("-" * 50)

        self._log("\n=== Test in Progress... ===")
        test_run_start_time = time.time()

        active_threads = []
        local_flooder_stdout_thread = None
        local_flooder_stderr_thread = None
        latency_monitor_started_successfully = False

        try:
            if self.measure_remote_rx:
                if not self.remote_ip or not self.remote_user or not self.remote_iface_name:
                    self._log(
                        "[ERROR] Remote IP, User, or Interface not specified for remote Rx measurement. Skipping remote agent.")
                elif not self._connect_ssh():
                    raise Exception("SSH Connection Failed for Remote Agent")
                elif not self._deploy_and_run_remote_agent(interval=1.0):
                    raise Exception("Remote Agent Deployment/Execution Failed")
                if self.ssh_stdout_thread: active_threads.append(self.ssh_stdout_thread)
                if self.ssh_stderr_thread: active_threads.append(self.ssh_stderr_thread)

            if self.remote_ip:
                if self._start_latency_monitor():
                    latency_monitor_started_successfully = True
                    if self.latency_thread: active_threads.append(self.latency_thread)
            else:
                self._log("[Info] No remote_ip provided, latency will not be monitored.", is_verbose=True)

            dst_mac = self.remote_mac if self.remote_mac else "ff:ff:ff:ff:ff:ff"
            try:
                pkg_name = __package__ if __package__ else 'throughputtool'
                with importlib.resources.path(pkg_name, 'l2_flooder') as flooder_path_obj:
                    flooder_path_str = str(flooder_path_obj)
            except (ModuleNotFoundError, FileNotFoundError, TypeError, Exception) as e_l2f_path:
                self._log(
                    f"[Warning] Could not locate 'l2_flooder' via package resources ({e_l2f_path}). Trying CWD/PATH.",
                    is_verbose=True)
                flooder_path_cwd = os.path.join(os.getcwd(), "l2_flooder")
                if os.path.exists(flooder_path_cwd) and os.access(flooder_path_cwd, os.X_OK):
                    flooder_path_str = flooder_path_cwd
                else:
                    flooder_path_str = "l2_flooder"

            self._log(f"[*] Using l2_flooder path: {flooder_path_str}", is_verbose=True)

            vlan_id_arg = "0"
            ethertype_for_flooder = self.ethertype_code

            if self.ethertype_str == "VLAN":
                vlan_id_arg = "auto"
                ethertype_for_flooder = "0x0800"
                if not self.verbose:
                    self._log("[Info] Generating VLAN-tagged traffic with cycling IDs (inner protocol: IPv4).")
                else:
                    self._log(
                        "[Info] 'VLAN' selected. Generating VLAN-tagged frames with cycling IDs (1-4094) and inner protocol IPv4 (0x0800).")

            cmd = ["sudo", flooder_path_str, self.iface, str(self.packet_size),
                   ethertype_for_flooder, dst_mac, str(self.target_l2_rate), vlan_id_arg]
            if not self.verbose:
                cmd.append("quiet")

            self._log(f"[*] Full command to execute: {' '.join(cmd)}", is_verbose=True)

            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, bufsize=1, preexec_fn=os.setsid)
            self._log(
                f"[*] Local l2_flooder process started with PID: {self.process.pid} (PGID: {os.getpgid(self.process.pid) if hasattr(os, 'getpgid') else 'N/A'})",
                is_verbose=True)

            local_flooder_stdout_thread = threading.Thread(target=self._parse_l2_flooder_output,
                                                           args=(self.process.stdout,), daemon=True)
            local_flooder_stderr_thread = threading.Thread(target=self._log_subprocess_output,
                                                           args=(self.process.stderr, "L2_FLOODER_STDERR"), daemon=True)

            local_flooder_stdout_thread.start();
            active_threads.append(local_flooder_stdout_thread)
            local_flooder_stderr_thread.start();
            active_threads.append(local_flooder_stderr_thread)

            time.sleep(0.3)
            if self.process.poll() is not None:
                raise Exception(
                    f"Local l2_flooder failed to start or exited immediately. RC: {self.process.returncode}")

            end_of_skip_period_ts = test_run_start_time + self.initial_skip_seconds

            while not self.stop_flag:
                stats1 = psutil.net_io_counters(pernic=True).get(self.iface)
                t1 = time.monotonic()
                if not stats1: self._log(f"[Error] Interface {self.iface} stats not found (stats1)."); break

                if self.stop_flag: break
                time.sleep(1.0)
                if self.stop_flag: break

                stats2 = psutil.net_io_counters(pernic=True).get(self.iface)
                t2 = time.monotonic()
                if not stats2: self._log(f"[Error] Interface {self.iface} stats not found (stats2)."); break

                time_diff = t2 - t1
                if time_diff <= 0: time_diff = 1.0

                local_tx_rate = ((stats2.bytes_sent - stats1.bytes_sent) * 8) / time_diff / 1_000_000
                local_rx_rate = ((stats2.bytes_recv - stats1.bytes_recv) * 8) / time_diff / 1_000_000

                current_data_collection_ts = time.time()

                self.data["local_tx"].append(local_tx_rate)
                self.data["local_rx"].append(local_rx_rate)
                self.data["timestamp"].append(current_data_collection_ts)

                if not self.high_load_warning_logged and self.target_l2_rate > 20:
                    throughput_for_check = local_tx_rate
                    # Use remote RX rate for check if it's available, as it's more accurate
                    if self.measure_remote_rx and self.data["remote_rx"]:
                        throughput_for_check = self.data["remote_rx"][-1]

                    # # Check if we are more than 3 seconds into the test to avoid false positives at startup
                    # if time.time() - test_run_start_time > 3 and throughput_for_check < (self.target_l2_rate * 0.8):
                    #     self._log(
                    #         "\n[Warning] System resource limit may be reached. Actual throughput is below target.")
                    #     self._log("           This can cause high latency and a delay when stopping the test.\n")
                    #     self.high_load_warning_logged = True

                actual_throughput_for_log = 0.0
                log_source_label = "(N/A)"
                latest_remote_rx_val_for_metrics = 0.0

                remote_rx_data_list = self.data.get("remote_rx", [])
                if self.measure_remote_rx and remote_rx_data_list:
                    actual_throughput_for_log = remote_rx_data_list[-1]
                    log_source_label = "(Remote Rx)"
                    latest_remote_rx_val_for_metrics = actual_throughput_for_log
                elif self.data["local_tx"]:
                    actual_throughput_for_log = self.data["local_tx"][-1]
                    log_source_label = "(Local Tx)"

                latest_latency_val_for_metrics = self.data["latency"][-1] if self.data.get("latency") else \
                    self.latency_config["ping_timeout_placeholder_ms"]

                self._log(
                    f"Target: {self.target_l2_rate:.2f} Mbps, Actual Throughput: {actual_throughput_for_log:.2f} Mbps {log_source_label}, Latency: {latest_latency_val_for_metrics if latest_latency_val_for_metrics != self.latency_config['ping_timeout_placeholder_ms'] else 'N/A'} ms")

                duration_for_metrics = current_data_collection_ts - test_run_start_time
                if self.update_metrics_callback:
                    self.update_metrics_callback(tx=local_tx_rate, rx=local_rx_rate,
                                                 latency=latest_latency_val_for_metrics,
                                                 loss=0.0,
                                                 duration_secs=int(duration_for_metrics),
                                                 total=actual_throughput_for_log,
                                                 remote_rx_val=latest_remote_rx_val_for_metrics)

                if self.graph and current_data_collection_ts >= end_of_skip_period_ts:
                    ts_all = self.data.get("timestamp", [])
                    plot_start_index = 0
                    for i, ts_val in enumerate(ts_all):
                        if ts_val >= end_of_skip_period_ts:
                            plot_start_index = i
                            break
                    else:
                        plot_start_index = len(ts_all)

                    if plot_start_index < len(ts_all):
                        ts_p = ts_all[plot_start_index:]
                        num_expected_points = len(ts_p)

                        lrx_raw = self.data.get("local_rx", [])[plot_start_index:]
                        lat_raw = self.data.get("latency", [])[plot_start_index:]
                        rrx_raw = self.data.get("remote_rx", [])[plot_start_index:] if self.measure_remote_rx else []

                        lrx_p = lrx_raw + [0.0] * (num_expected_points - len(lrx_raw))
                        lat_padded = lat_raw + [self.latency_config["ping_timeout_placeholder_ms"]] * (
                                    num_expected_points - len(lat_raw))
                        lat_p = [val if val != self.latency_config["ping_timeout_placeholder_ms"] else 0 for val in
                                 lat_padded]

                        if self.measure_remote_rx:
                            rrx_p = rrx_raw + [0.0] * (num_expected_points - len(rrx_raw))
                        else:
                            rrx_p = [0.0] * num_expected_points

                        if num_expected_points > 0:
                            self.graph.update_graphs(ts_p, lrx_p, rrx_p, lat_p)

        except Exception as e:
            self._log(f"[ERROR] Test execution failed: {e}")
            self._log(traceback.format_exc(), is_verbose=True)
        finally:
            self._log("[*] Test loop finished or interrupted. Starting cleanup...", is_verbose=True)
            self.latency_stop_flag = True

            if self.process and self.process.poll() is None:
                pgid_to_terminate = 0
                try:
                    pgid_to_terminate = os.getpgid(self.process.pid) if hasattr(os, 'getpgid') else self.process.pid
                    self._log(
                        f"[*] Terminating local l2_flooder process group (PGID {pgid_to_terminate}) with SIGTERM...",
                        is_verbose=True)
                    os.killpg(pgid_to_terminate, signal.SIGTERM)
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._log(
                        f"[Warning] Local l2_flooder PGID {pgid_to_terminate} unresponsive to SIGTERM. Sending SIGKILL...",
                        is_verbose=True)
                    try:
                        os.killpg(pgid_to_terminate, signal.SIGKILL)
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._log(f"[Warning] Local l2_flooder PGID {pgid_to_terminate} did not exit after SIGKILL.",
                                  is_verbose=True)
                    except (ProcessLookupError, PermissionError) as e_kill_pg:
                        self._log(f"[Warning] os.killpg SIGKILL for PGID {pgid_to_terminate} failed: {e_kill_pg}",
                                  is_verbose=True)
                    except Exception as e_kill_generic:
                        self._log(
                            f"[Error] Exception during os.killpg SIGKILL for PGID {pgid_to_terminate}: {e_kill_generic}",
                            is_verbose=True)
                except (ProcessLookupError, PermissionError) as e_term_pg:
                    self._log(
                        f"[Warning] os.killpg SIGTERM for PGID {pgid_to_terminate} failed: {e_term_pg}. Process might have already exited.",
                        is_verbose=True)
                except Exception as e_term_generic:
                    self._log(f"[Error] Exception during SIGTERM for PGID {pgid_to_terminate}: {e_term_generic}",
                              is_verbose=True)

                if self.process and self.process.poll() is None:
                    self._log(
                        f"[Warning] l2_flooder (PID {self.process.pid}) still running after PGID signals. Attempting direct terminate/kill and then 'sudo killall'.",
                        is_verbose=True)
                    try:
                        self.process.terminate()
                        self.process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=1)
                    except Exception:
                        pass

                    try:
                        killall_cmd = ["sudo", "killall", "-9", "l2_flooder"]
                        self._log(f"[*] Executing fallback: {' '.join(killall_cmd)}", is_verbose=True)
                        subprocess.run(killall_cmd, timeout=2, check=False, capture_output=True, text=True)
                    except Exception as e_killall:
                        self._log(f"[Error] Fallback 'sudo killall l2_flooder' failed: {e_killall}", is_verbose=True)
            self.process = None

            for th in active_threads:
                if th and th.is_alive():
                    if self.verbose: self._log(f"[*] Joining thread: {th.name} (Timeout: 1.0s)", is_verbose=True)
                    try:
                        th.join(timeout=1.0)
                        if th.is_alive() and self.verbose: self._log(f"[Warning] Thread {th.name} did not join.",
                                                                     is_verbose=True)
                    except Exception as e_join:
                        self._log(f"[Warning] Error joining thread {th.name}: {e_join}", is_verbose=True)

            if self.latency_thread and self.latency_thread.is_alive() and self.latency_thread not in active_threads:
                if self.verbose: self._log(f"[*] Joining latency thread explicitly... (Timeout: 1.0s)", is_verbose=True)
                self.latency_thread.join(timeout=1.0)
                if self.latency_thread.is_alive() and self.verbose: self._log(
                    f"[Warning] Latency thread did not join after explicit attempt.", is_verbose=True)

            if self.measure_remote_rx and self.ssh_client:
                self._stop_remote_agent()
                self._disconnect_ssh()

            self._final_summary(test_run_start_time)

    def _final_summary(self, test_run_start_time):
        duration = int(time.time() - test_run_start_time)
        mins, secs = divmod(duration, 60)

        all_local_tx = self.data.get("local_tx", [])
        all_remote_rx = self.data.get("remote_rx", [])
        all_latency = self.data.get("latency", [])

        skip_n_points = self.initial_skip_seconds

        local_tx_for_summary = all_local_tx[skip_n_points:] if len(all_local_tx) > skip_n_points else []
        remote_rx_for_summary = []
        if self.measure_remote_rx:
            remote_rx_for_summary = all_remote_rx[skip_n_points:] if len(all_remote_rx) > skip_n_points else []

        latency_for_summary_stats = []
        if self.remote_ip:
            latency_for_summary_stats = all_latency[skip_n_points:] if len(all_latency) > skip_n_points else []

        valid_latency_for_summary_stats_numeric = [l for l in latency_for_summary_stats if
                                                   l != self.latency_config["ping_timeout_placeholder_ms"]]

        primary_throughput_series_for_stats = []
        throughput_source_for_stats = "(N/A)"

        if self.measure_remote_rx and remote_rx_for_summary:
            primary_throughput_series_for_stats = remote_rx_for_summary
            throughput_source_for_stats = "(Remote Rx)"
        elif local_tx_for_summary:
            primary_throughput_series_for_stats = local_tx_for_summary
            throughput_source_for_stats = "(Local Tx)"

        min_tp_str, avg_tp_str, max_tp_str = "N/A", "N/A", "N/A"

        meaningful_tp_stats = [x for x in primary_throughput_series_for_stats if
                               isinstance(x, (int, float)) and x > 0.01]

        if meaningful_tp_stats:
            min_tp_str = f"{min(meaningful_tp_stats):.2f} Mbps"
            avg_tp_str = f"{statistics.mean(meaningful_tp_stats):.2f} Mbps"
            max_tp_str = f"{max(meaningful_tp_stats):.2f} Mbps"
        elif primary_throughput_series_for_stats:
            all_numeric_in_valid_series = [x for x in primary_throughput_series_for_stats if
                                           isinstance(x, (int, float))]
            if all_numeric_in_valid_series:
                min_tp_str = f"{min(all_numeric_in_valid_series):.2f} Mbps"
                avg_tp_str = f"{statistics.mean(all_numeric_in_valid_series):.2f} Mbps"
                max_tp_str = f"{max(all_numeric_in_valid_series):.2f} Mbps"

        details = [
            ("Test Duration", f"{mins:02}:{secs:02} (mm:ss)"),
            ("Target L2 Rate", f"{self.target_l2_rate:.2f} Mbps"),
            ("Packet Size", f"{self.packet_size} bytes"),
            ("EtherType", f"{self.ethertype_str} ({self.ethertype_code})"),
            ("Destination MAC", self.remote_mac if self.remote_mac else "Broadcast/ff:ff:ff:ff:ff:ff"),
        ]
        if self.remote_ip:
            details.append(("Remote Target (Ping/Agent)", self.remote_ip))

        details.append(("", ""))
        details.append(("--- Actual Throughput Statistics ---", None))
        details.append((f"  Min Throughput {throughput_source_for_stats}", min_tp_str))
        details.append((f"  Avg Throughput {throughput_source_for_stats}", avg_tp_str))
        details.append((f"  Max Throughput {throughput_source_for_stats}", max_tp_str))

        if self.remote_ip:
            avg_lat_str, min_lat_str, max_lat_str = "N/A", "N/A", "N/A"

            timeout_count_for_stats = latency_for_summary_stats.count(
                self.latency_config["ping_timeout_placeholder_ms"])

            if valid_latency_for_summary_stats_numeric:
                avg_lat = statistics.mean(valid_latency_for_summary_stats_numeric)
                min_lat = min(valid_latency_for_summary_stats_numeric)
                max_lat = max(valid_latency_for_summary_stats_numeric)
                avg_lat_str = f"{avg_lat:.2f} ms"
                min_lat_str = f"{min_lat:.2f} ms"
                max_lat_str = f"{max_lat:.2f} ms"

            if timeout_count_for_stats > 0:
                timeout_info = f" ({timeout_count_for_stats} timeouts)"
                if not valid_latency_for_summary_stats_numeric:
                    avg_lat_str = f"N/A{timeout_info}"
                    min_lat_str = f"N/A{timeout_info}"
                    max_lat_str = f"> {self.latency_config['ping_packet_timeout_s']:.0f}s{timeout_info}"
                else:
                    max_lat_str += timeout_info

            details.extend([
                ("", ""),
                ("--- Latency Statistics (Ping to Remote IP) ---", None),
                ("  Average RTT", avg_lat_str),
                ("  Min RTT", min_lat_str),
                ("  Max RTT", max_lat_str),
            ])
        elif self.remote_ip:
            details.extend([
                ("", ""),
                ("--- Latency Statistics (Ping to Remote IP) ---", None),
                ("  Average RTT", "N/A (No valid data after skip)"),
            ])

        self._log("\n" + "+" + "-" * 70 + "+")
        self._log(f"| {'L2 Traffic Test Summary'.center(70)} |")
        self._log("+" + "-" * 70 + "+")

        max_key_len = 0
        for key, _ in details:
            if key and "---" not in key:
                max_key_len = max(max_key_len, len(key))

        for key, value in details:
            if "---" in key and value is None:
                self._log(f"| {key.center(70)} |")
            elif not key and not value:
                self._log(f"| {' '.ljust(70)} |")
            else:
                padded_key = (key if key is not None else "").ljust(max_key_len)
                str_value = str(value) if value is not None else ""
                line_content = f"  {padded_key} : {str_value}"
                self._log(f"| {line_content.ljust(70)} |")
        self._log("+" + "-" * 70 + "+")

        if self.ui:
            for key in ['start_button', 'stop_button', 'status_bar',
                        'export_log_btn', 'save_tp_graph_btn', 'save_latency_graph_btn']:
                widget = self.ui.get(key)
                if widget:
                    try:
                        if key == 'start_button':
                            widget.config(state="normal")
                        elif key == 'stop_button':
                            widget.config(state="disabled")
                        elif key == 'status_bar':
                            widget.config(text="Test Stopped")
                        elif key in ['export_log_btn', 'save_tp_graph_btn', 'save_latency_graph_btn']:
                            widget.config(state="normal")
                    except Exception as e_ui:
                        self._log(f"[UI Error] Configuring '{key}': {e_ui}", is_verbose=True)