# Inside throughputtool/l2_traffic_test.py

import subprocess
import threading
import time
import psutil
import netifaces # Make sure this import is present
import importlib.resources # Add this import

class L2TrafficTest:
    def __init__(self, iface, ui_refs, ethertype="All", remote_mac="", packet_size=1400, profile="Moderate",
                 data_store=None, graph=None, update_metrics=None):
        # ... (rest of your __init__ method) ...
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


    # ... (other methods like run, stop, _get_iface_ip, _get_iface_mac) ...

    def _run_test(self):
        self._log("\n=== L2/L3 Traffic Test Started ===\n")
        start_time = time.time()

        dst_mac = self.remote_mac if self.remote_mac else "ff:ff:ff:ff:ff:ff"
        ethertype_hex = self.ethertype_code # Use the stored self.ethertype_code

        try:
            # For Python 3.7, 3.8 (also works on 3.9+)
            with importlib.resources.path('throughputtool', 'l2_flooder') as flooder_executable_path:
                flooder_path_str = str(flooder_executable_path)
        except (ModuleNotFoundError, FileNotFoundError): # Broader exception for resource not found
            self._log("[ERROR] Could not locate 'l2_flooder' executable within the package. Please ensure it's correctly packaged.")
            self._log("[Warning] Attempting to run 'l2_flooder' from current directory (./l2_flooder) as a fallback.")
            flooder_path_str = "l2_flooder"  # Fallback, less reliable

        cmd = ["sudo", flooder_path_str, self.iface, str(self.packet_size), ethertype_hex, dst_mac]
        self._log(f"[*] Executing L2 Flooder: {' '.join(cmd)}") # Log the command for debugging

        try:
            self.process = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) # Capture stderr
            # Optional: Add a small delay to check if process started successfully
            time.sleep(0.1)
            if self.process.poll() is not None: # Process terminated immediately
                stderr_output = self.process.stderr.read().decode('utf-8', errors='ignore') if self.process.stderr else "No stderr"
                self._log(f"[ERROR] l2_flooder failed to start or exited immediately. Return code: {self.process.returncode}. Stderr: {stderr_output}")
                self._final_summary(start_time)
                return
        except FileNotFoundError:
            self._log(f"[ERROR] l2_flooder executable not found at '{flooder_path_str}'. Cannot start L2/L3 test.")
            self._final_summary(start_time)
            return
        except Exception as e:
            self._log(f"[ERROR] Failed to start l2_flooder: {e}")
            self._final_summary(start_time)
            return


        while not self.stop_flag:
            # ... (rest of your _run_test loop)
            # Ensure stats1 and stats2 correctly reference self.iface
            try:
                stats1 = psutil.net_io_counters(pernic=True).get(self.iface)
                if stats1 is None:
                    self._log(f"[Error] Interface {self.iface} not found in psutil stats (stats1). Stopping L2 test.")
                    break
                t1 = time.time()
                time.sleep(1) # Consider making this configurable or dynamic
                stats2 = psutil.net_io_counters(pernic=True).get(self.iface)
                if stats2 is None:
                    self._log(f"[Error] Interface {self.iface} not found in psutil stats (stats2). Stopping L2 test.")
                    break
                t2 = time.time()

                # Ensure t2 > t1 to prevent division by zero
                if t2 <= t1:
                     time_diff = 0.001 # a small non-zero value
                else:
                     time_diff = t2-t1

                rx_rate = (stats2.bytes_recv - stats1.bytes_recv) * 8 / time_diff / 1e6  # Mbps
                duration = int(t2 - start_time)

                self.data["tx"].append(0.0)
                self.data["rx"].append(rx_rate)
                self.data["latency"].append(0.0)
                self.data["throughput"].append(rx_rate)
                self.data["timestamp"].append(time.time())

                if self.update_metrics:
                    self.update_metrics(tx=0.0, rx=rx_rate, latency=0.0, loss=0.0, duration_secs=duration, total=rx_rate)

                if self.graph: # Check if graph object exists
                    self.graph.update_graphs(
                        self.data["timestamp"],
                        self.data["tx"],
                        self.data["rx"],
                        self.data["latency"]
                    )

                self._log(f"[+] Rx: {rx_rate:.2f} Mbps") # Simplified log
            except KeyError as e: # Handle if iface disappears from psutil
                self._log(f"[Error] Interface {self.iface} disappeared or psutil error: {e}. Stopping L2 test.")
                break
            except Exception as e: # Catch other unexpected errors in the loop
                self._log(f"[Error] Unexpected error in L2 test loop: {e}")
                break


        if self.process and self.process.poll() is None:
            try:
                self._log("[*] Terminating l2_flooder process...")
                # Send SIGTERM first using sudo for permission
                subprocess.run(["sudo", "kill", str(self.process.pid)], timeout=2, check=False)
                self.process.wait(timeout=2) # Wait for termination
            except subprocess.TimeoutExpired:
                self._log("[Warning] l2_flooder did not terminate gracefully, sending SIGKILL...")
                subprocess.run(["sudo", "kill", "-9", str(self.process.pid)], check=False) # Force kill
            except Exception as e:
                self._log(f"[Error] Failed to stop l2_flooder process: {e}")
        self.process = None # Clear the process

        self._final_summary(start_time) # Call _final_summary at the end

    def _final_summary(self, start_time): # Ensure this method exists and is correct
        duration = int(time.time() - start_time)
        mins, secs = divmod(duration, 60)
        final_rx = self.data['rx'][-1] if self.data['rx'] else 0.0

        self._log("\n--- L2/L3 Traffic Test Stopped ---")
        self._log(f"Test Duration        : {mins:02}:{secs:02} (mm:ss)")
        self._log(f"Final Live Rx        : {final_rx:.2f} Mbps")

        if self.ui and self.ui.get('start_button'): self.ui['start_button'].config(state="normal")
        if self.ui and self.ui.get('stop_button'): self.ui['stop_button'].config(state="disabled")
        if self.ui and self.ui.get('status_bar'): self.ui['status_bar'].config(text="Test Stopped")

        try:
            if self.ui and self.ui.get('export_log_btn'): self.ui['export_log_btn'].config(state="normal")
            if self.ui and self.ui.get('save_tp_graph_btn'): self.ui['save_tp_graph_btn'].config(state="normal")
            if self.ui and self.ui.get('save_latency_graph_btn'): self.ui['save_latency_graph_btn'].config(state="normal")
        except Exception as e:
            self._log(f"[UI Error] Failed to enable buttons: {e}")


    def _log(self, msg): # Ensure this method exists and is correct
        if self.ui and self.ui.get('output_area'):
            try:
                self.ui['output_area'].config(state='normal')
                self.ui['output_area'].insert("end", msg + "\n")
                self.ui['output_area'].config(state='disabled')
                self.ui['output_area'].see("end")
            except Exception: # Fallback if UI logging fails
                print(msg)
        else:
            print(msg)