# main.py

import ttkbootstrap as tb
import psutil
from tkinter import ttk  # Ensure ttk is imported if any direct ttk.Widget calls remain (though ui_layout handles most)
from ui_layout import create_layout
from graph_manager import GraphManager
from iperf_test import IperfTest
from flood_ping_test import FloodPingTest
from utils import export_log, save_graph
from l2_traffic_test import L2TrafficTest
import time
import warnings

warnings.filterwarnings("ignore", category=DeprecationWarning)  # For paramiko warnings


def main():
    app = tb.Window(themename="flatly")
    # app.title and app.attributes set in create_layout

    try:
        interfaces = list(psutil.net_io_counters(pernic=True).keys())
    except Exception as e:
        print(f"Error fetching interfaces: {e}. Defaulting to empty list.")
        interfaces = []

    ui = create_layout(app, interfaces)  # ui dictionary holds all relevant widgets/vars
    graph = GraphManager(ui['graph_frames'])

    data_store = {
        'local_tx': [], 'local_rx': [], 'remote_rx': [],
        'latency': [], 'timestamp': [], 'throughput': []
    }

    test_runner = {'instance': None}

    def update_metrics(tx, rx, latency, loss, duration_secs, total, remote_rx_val=0.0):
        # This function updates the text part of the labels.
        # The labels themselves (e.g., "Local Tx: N/A") are created in ui_layout.py
        if ui.get('metrics_labels'):
            metrics = ui['metrics_labels']

            # Update values, assuming label prefixes are set in ui_layout.py
            if metrics.get('duration'): metrics['duration'].config(
                text=f"Duration: {duration_secs // 60:02}:{duration_secs % 60:02}")
            if metrics.get('latency'): metrics['latency'].config(text=f"Latency: {latency:.2f} ms")
            if metrics.get('loss'): metrics['loss'].config(text=f"Loss: {loss:.2f}%")

            if metrics.get('local_tx'): metrics['local_tx'].config(text=f"Local Tx: {tx:.2f} Mbps")
            if metrics.get('local_rx'): metrics['local_rx'].config(text=f"Local Rx: {rx:.2f} Mbps")

            # Remote Rx label's text is updated here. Its visibility is handled by ui_layout.update_visibility
            if metrics.get('remote_rx'): metrics['remote_rx'].config(text=f"Remote Rx: {remote_rx_val:.2f} Mbps")

            if metrics.get('total'): metrics['total'].config(text=f"Total Bw: {total:.2f} Mbps")

        # Ensure UI updates from threads are handled safely if necessary
        # For Tkinter, if this callback is assigned to a test instance running in a thread,
        # and then this function (which is in the main thread scope) is called by the test instance,
        # it should be fine. If update_metrics itself was directly run in another thread,
        # then app.after() would be needed for Tkinter calls.

    def start_test():
        if ui['output_area']:
            ui['output_area'].config(state='normal')
            ui['output_area'].delete('1.0', 'end')
            ui['output_area'].config(state='disabled')

        for btn_key in ['export_log_btn', 'save_tp_graph_btn', 'save_latency_graph_btn']:
            if ui.get(btn_key): ui[btn_key].config(state="disabled")

        # Fetch values (ensure keys match what's in ui['entries'] from ui_layout.py)
        remote_ip = ui['entries']['Remote IP'].get()
        loss_thresh_str = ui['entries']['Loss Threshold (%)'].get()
        latency_thresh_str = ui['entries']['Latency Threshold (ms)'].get()
        remote_mac = ui['entries']["Remote MAC"].get()
        target_l2_rate_str = ui['entries']["Target L2 Rate (Mbps)"].get()

        remote_user = ui['entries']["Remote Username"].get()
        remote_pass = ui['entries']["Remote Password"].get()
        remote_iface = ui['entries']["Remote Interface"].get()

        iface = ui['iface_var'].get()
        profile = ui['profile_var'].get()
        traffic = ui['traffic_var'].get()
        protocol = ui['protocol_var'].get()
        direction = ui['direction_var'].get()
        packet_size_str = ui['packet_size_var'].get()
        ethertype = ui['ethertype_var'].get()

        try:
            packet_size = int(packet_size_str) if packet_size_str else 1400
            latency_thresh = float(latency_thresh_str) if latency_thresh_str else 70.0
            loss_thresh = float(loss_thresh_str) if loss_thresh_str else 10.0
            target_l2_rate = float(target_l2_rate_str) if target_l2_rate_str else 50.0
        except ValueError:
            log_msg_to_ui = "[ERROR] Invalid numeric input for packet size, threshold, or L2 rate.\n"
            if ui.get('output_area'):
                ui['output_area'].config(state='normal')
                ui['output_area'].insert('end', log_msg_to_ui)
                ui['output_area'].config(state='disabled')
            else:
                print(log_msg_to_ui)
            if ui.get('status_bar'): ui['status_bar'].config(text="Error: Invalid Input")
            return

        for key_data in data_store: data_store[key_data].clear()
        graph.update_graphs([], [], [], [])  # Reset graph

        if ui.get('start_button'): ui['start_button'].config(state='disabled')
        if ui.get('stop_button'): ui['stop_button'].config(state='normal')
        if ui.get('status_bar'): ui['status_bar'].config(text=f"Test Running: {traffic}...")

        # ui_refs_for_test can just be the 'ui' dict itself, as test classes access specific keys.
        # The L2TrafficTest _log method now directly uses self.ui.

        if traffic == "Flood Ping":
            tester = FloodPingTest(remote_ip, iface, packet_size, profile,
                                   latency_thresh, loss_thresh,
                                   ui,  # Pass the main ui dict
                                   data_store, graph,
                                   update_metrics=update_metrics)
        elif traffic == "L2/L3 Traffic":
            measure_remote = bool(remote_ip and remote_user and remote_iface)
            tester = L2TrafficTest(
                iface=iface, ui_refs=ui, ethertype=ethertype, remote_mac=remote_mac,
                packet_size=packet_size, target_l2_rate=target_l2_rate, profile=profile,
                data_store=data_store, graph=graph, update_metrics=update_metrics,
                remote_ip=remote_ip, remote_user=remote_user, remote_pass=remote_pass,
                remote_iface_name=remote_iface, measure_remote_rx=measure_remote,
                verbose=True  # Set True for debugging
            )
        else:  # iperf3
            tester = IperfTest(
                remote_ip=remote_ip, iface=iface, protocol=protocol,
                packet_size=str(packet_size), direction=direction, profile=profile,
                ui_refs=ui, data_store=data_store, graph=graph,
                update_metrics=update_metrics, verbose_logging=False
            )

        test_runner['instance'] = tester
        if ui.get('stop_button'): ui['stop_button'].config(command=test_runner['instance'].stop)

        tester.run()

    if ui.get('start_button'): ui['start_button'].config(command=start_test)
    if ui.get('export_log_btn'): ui['export_log_btn'].config(command=lambda: export_log(ui['output_area']))
    if ui.get('save_tp_graph_btn'): ui['save_tp_graph_btn'].config(
        command=lambda: save_graph(graph.fig1, "throughput_graph"))
    if ui.get('save_latency_graph_btn'): ui['save_latency_graph_btn'].config(
        command=lambda: save_graph(graph.fig2, "latency_graph"))

    app.mainloop()


if __name__ == "__main__":
    main()