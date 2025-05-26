# l2_traffic_test.py

import subprocess
import threading
import time
import psutil
import importlib.resources
import os
import paramiko  # For SSH functionality
import traceback
import tkinter as tk  # Added for _log method

# --- Constants ---
REMOTE_SCRIPT_NAME = "remote_rx_agent.py"  # Ensure this file exists where the script can find it
REMOTE_PYTHON_EXEC = "python3"  # Command to run python3 on the remote machine


class L2TrafficTest:
    def __init__(self, iface, ui_refs, ethertype="All", remote_mac="", packet_size=1400,
                 target_l2_rate=50.0,  # Added: Target L2 rate in Mbps
                 profile="Moderate",  # Currently unused in L2 test but kept for consistency
                 data_store=None, graph=None, update_metrics=None,
                 remote_ip="", remote_user="", remote_pass="", remote_iface_name="",
                 measure_remote_rx=False, verbose=False):

        self.iface = iface
        self.ui = ui_refs
        self.ethertype_str = ethertype
        self.remote_mac = remote_mac.strip()
        self.packet_size = int(packet_size)
        self.target_l2_rate = float(target_l2_rate)  # Store the target rate
        self.profile = profile
        self.verbose = verbose

        self.data = data_store if data_store is not None else {}
        self.data.setdefault("local_tx", self.data.pop("tx", []))
        self.data.setdefault("local_rx", self.data.pop("rx", []))
        for key_to_ensure in ["latency", "timestamp", "throughput"]:
            self.data.setdefault(key_to_ensure, [])
        if measure_remote_rx:
            self.data.setdefault("remote_rx", [])

        self.graph = graph
        self.update_metrics_callback = update_metrics

        ethertype_map = {
            "IPv4": "0x0800", "ARP": "0x0806", "IPv6": "0x86DD",
            "VLAN": "0x8100", "MPLS": "0x8847", "PPPoE": "0x8864",
            "Loopback": "0x9000", "Unknown": "0x88B5", "0xFFFF": "0xFFFF"
        }
        self.ethertype_code = ethertype_map.get(self.ethertype_str, "0x0800")

        self.remote_ip = remote_ip
        self.remote_user = remote_user
        self.remote_pass = remote_pass
        self.remote_iface_name = remote_iface_name
        self.measure_remote_rx = measure_remote_rx
        self.ssh_client = None
        self.ssh_channel = None
        self.ssh_stdout_thread = None
        self.ssh_stderr_thread = None
        self.remote_rx_pid = None

        self.stop_flag = False
        self.process = None
        self.thread = None

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
        self._log("[*] L2/L3 Traffic Test thread starting...")
        self.stop_flag = False
        for key in ["local_tx", "local_rx", "latency", "timestamp", "throughput", "remote_rx"]:
            self.data.setdefault(key, []).clear()

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
        if self.ui and self.ui.get('status_bar'): self.ui['status_bar'].config(text="Test Stopping...")

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
                    self._log(f"[ERROR] '{REMOTE_SCRIPT_NAME}' not found in CWD (__file__ undefined)."); return False

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

                        self.data.setdefault("remote_rx", []).append(remote_rx_mbps)

                        if self.ui and self.ui.get('metrics_labels') and self.ui['metrics_labels'].get('remote_rx'):
                            try:  # Direct UI update (ensure thread-safe if needed, e.g. app.after for Tkinter)
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
                    cmd_kill = f"kill -2 {self.remote_rx_pid}"  # SIGINT
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

    def _log_subprocess_output(self, pipe, pipe_name_prefix):
        try:
            for line in iter(pipe.readline, ''):  # Read until pipe closes
                if line: self._log(f"[{pipe_name_prefix}] {line.strip()}", is_verbose=self.verbose)
        except ValueError:
            self._log(f"[{pipe_name_prefix}] Pipe closed or error during read.", is_verbose=True)
        except Exception as e:
            self._log(f"[{pipe_name_prefix}] Error reading output: {e}", is_verbose=True)
        finally:
            if pipe: pipe.close()

    def _run_test(self):
        self._log("\n=== L2/L3 Traffic Test Started ===")
        start_time = time.time()

        active_threads = []
        local_flooder_stdout_thread = None
        local_flooder_stderr_thread = None

        try:
            if self.measure_remote_rx:
                if not self.remote_ip or not self.remote_user or not self.remote_iface_name:
                    self._log(
                        "[ERROR] Remote IP, User, or Interface not specified for remote Rx measurement. Skipping.")
                    self.measure_remote_rx = False  # Disable it for this run
                elif not self._connect_ssh():
                    raise Exception("SSH Connection Failed")
                elif not self._deploy_and_run_remote_agent(interval=1.0):
                    raise Exception("Remote Agent Deployment/Execution Failed")

                if self.ssh_stdout_thread: active_threads.append(self.ssh_stdout_thread)
                if self.ssh_stderr_thread: active_threads.append(self.ssh_stderr_thread)

            dst_mac = self.remote_mac if self.remote_mac else "ff:ff:ff:ff:ff:ff"
            try:
                # Try to resolve path using package resources first
                pkg_name = __package__ if __package__ else 'throughputtool'  # Guess package name
                with importlib.resources.path(pkg_name, 'l2_flooder') as flooder_path_obj:
                    flooder_path_str = str(flooder_path_obj)
            except (ModuleNotFoundError, FileNotFoundError, TypeError, Exception) as e_l2f_path:
                self._log(
                    f"[Warning] Could not locate 'l2_flooder' via package resources ({e_l2f_path}). Trying CWD/PATH.",
                    is_verbose=True)
                # Fallback: Check current working directory
                flooder_path_cwd = os.path.join(os.getcwd(), "l2_flooder")
                if os.path.exists(flooder_path_cwd) and os.access(flooder_path_cwd, os.X_OK):
                    flooder_path_str = flooder_path_cwd
                else:
                    flooder_path_str = "l2_flooder"  # Final fallback: assume it's in PATH

            self._log(f"[*] Using l2_flooder path: {flooder_path_str}", is_verbose=True)
            # Pass self.target_l2_rate to l2_flooder
            cmd = ["sudo", flooder_path_str, self.iface, str(self.packet_size),
                   self.ethertype_code, dst_mac, str(self.target_l2_rate)]
            self._log(f"[*] Full command to execute: {' '.join(cmd)}", is_verbose=True)

            self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
            self._log(f"[*] Local l2_flooder process started with PID: {self.process.pid}", is_verbose=True)

            local_flooder_stdout_thread = threading.Thread(target=self._log_subprocess_output,
                                                           args=(self.process.stdout, "L2_FLOODER_STDOUT"), daemon=True)
            local_flooder_stderr_thread = threading.Thread(target=self._log_subprocess_output,
                                                           args=(self.process.stderr, "L2_FLOODER_STDERR"), daemon=True)
            local_flooder_stdout_thread.start();
            active_threads.append(local_flooder_stdout_thread)
            local_flooder_stderr_thread.start();
            active_threads.append(local_flooder_stderr_thread)

            time.sleep(0.3)  # Increased slightly
            if self.process.poll() is not None:
                raise Exception(
                    f"Local l2_flooder failed to start or exited immediately. RC: {self.process.returncode}")

            while not self.stop_flag:
                stats1 = psutil.net_io_counters(pernic=True).get(self.iface)
                t1 = time.monotonic()
                if not stats1: self._log(f"[Error] Interface {self.iface} stats not found (stats1)."); break

                if self.stop_flag: break
                time.sleep(1.0)  # Main measurement interval
                if self.stop_flag: break

                stats2 = psutil.net_io_counters(pernic=True).get(self.iface)
                t2 = time.monotonic()
                if not stats2: self._log(f"[Error] Interface {self.iface} stats not found (stats2)."); break

                time_diff = t2 - t1
                if time_diff <= 0: time_diff = 1.0  # Avoid div by zero, use nominal interval

                local_tx_rate = ((stats2.bytes_sent - stats1.bytes_sent) * 8) / time_diff / 1_000_000
                local_rx_rate = ((stats2.bytes_recv - stats1.bytes_recv) * 8) / time_diff / 1_000_000

                current_ts_for_data = time.time()
                duration = int(current_ts_for_data - start_time)

                self.data["local_tx"].append(local_tx_rate)
                self.data["local_rx"].append(local_rx_rate)
                self.data["latency"].append(0.0)  # L2 test doesn't measure latency directly yet
                self.data["timestamp"].append(current_ts_for_data)
                self.data["throughput"].append(local_tx_rate)  # Primary throughput often considered as Tx

                latest_remote_rx = 0.0
                if self.measure_remote_rx and self.data.get("remote_rx"):  # Use .get for safety
                    try:
                        latest_remote_rx = self.data["remote_rx"][-1]
                    except IndexError:
                        pass  # Keep 0.0 if list is empty

                if self.update_metrics_callback:
                    self.update_metrics_callback(tx=local_tx_rate, rx=local_rx_rate, latency=0.0, loss=0.0,
                                                 duration_secs=duration, total=local_tx_rate,  # Total based on local Tx
                                                 remote_rx_val=latest_remote_rx)
                if self.graph:  # Pass only the data GraphManager expects
                    self.graph.update_graphs(self.data["timestamp"], self.data["local_tx"],
                                             self.data["local_rx"], self.data["latency"])

                self._log(f"[Target: {self.target_l2_rate:.2f} Mbps] "
                          f"Local Tx: {local_tx_rate:.2f} Mbps, Local Rx: {local_rx_rate:.2f} Mbps" +
                          (f", Remote Rx: {latest_remote_rx:.2f} Mbps" if self.measure_remote_rx else ""))

        except Exception as e:
            self._log(f"[ERROR] Test execution failed: {e}")
            self._log(traceback.format_exc(), is_verbose=True)  # Log full traceback if verbose
        finally:
            self._log("[*] Test loop finished or interrupted. Starting cleanup...", is_verbose=True)

            if self.process and self.process.poll() is None:
                self._log(f"[*] Terminating local l2_flooder process (PID {self.process.pid})...", is_verbose=True)
                try:
                    self.process.terminate()  # SIGTERM
                    self.process.wait(timeout=5)  # Increased timeout for l2_flooder exit
                except subprocess.TimeoutExpired:
                    self._log(
                        f"[Warning] Local l2_flooder (PID {self.process.pid}) unresponsive to SIGTERM. Sending SIGKILL...",
                        is_verbose=True)
                    self.process.kill()  # SIGKILL
                    try:
                        self.process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        self._log(f"[Warning] Local l2_flooder (PID {self.process.pid}) did not exit after SIGKILL.",
                                  is_verbose=True)
                except Exception as e_kill:
                    self._log(f"[Error] Failed to stop local l2_flooder: {e_kill}", is_verbose=True)

                if self.process and self.process.poll() is None:  # Still running?
                    self._log(
                        f"[Warning] l2_flooder (PID {self.process.pid}) still running. Attempting 'sudo killall l2_flooder'.",
                        is_verbose=True)
                    try:
                        killall_cmd = ["sudo", "killall", "l2_flooder"]
                        self._log(f"[*] Executing: {' '.join(killall_cmd)}", is_verbose=True)
                        subprocess.run(killall_cmd, timeout=2, check=False, capture_output=True, text=True)
                    except Exception as e_killall:
                        self._log(f"[Error] 'sudo killall l2_flooder' failed: {e_killall}", is_verbose=True)
            self.process = None

            # Join all started threads
            for th_name in ['local_flooder_stdout_thread', 'local_flooder_stderr_thread',
                            'ssh_stdout_thread', 'ssh_stderr_thread']:
                th = getattr(self, th_name, None)
                if th and th.is_alive():
                    if self.verbose: self._log(f"[*] Joining thread: {th.name}", is_verbose=True)
                    try:
                        th.join(timeout=0.5)
                    except Exception as e_join:
                        self._log(f"[Warning] Error joining thread {th.name}: {e_join}", is_verbose=True)

            if self.measure_remote_rx and self.ssh_client:
                self._stop_remote_agent()  # Ensures its threads are joined if still alive
                self._disconnect_ssh()

            self._final_summary(start_time)

    def _final_summary(self, start_time):
        duration = int(time.time() - start_time)
        mins, secs = divmod(duration, 60)

        final_local_tx = self.data.get('local_tx', [])[-1] if self.data.get('local_tx') else 0.0
        final_local_rx = self.data.get('local_rx', [])[-1] if self.data.get('local_rx') else 0.0
        final_remote_rx = 0.0
        if self.measure_remote_rx:
            final_remote_rx = self.data.get('remote_rx', [])[-1] if self.data.get('remote_rx') else 0.0

        self._log("\n--- L2/L3 Traffic Test Stopped ---")
        self._log(f"Test Duration        : {mins:02}:{secs:02} (mm:ss)")
        self._log(f"Final Live Local Tx  : {final_local_tx:.2f} Mbps")
        self._log(f"Final Live Local Rx  : {final_local_rx:.2f} Mbps")
        if self.measure_remote_rx:
            self._log(f"Final Live Remote Rx : {final_remote_rx:.2f} Mbps")

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
                        else:
                            widget.config(state="normal")
                    except Exception as e_ui:
                        self._log(f"[UI Error] Configuring '{key}': {e_ui}", is_verbose=True)