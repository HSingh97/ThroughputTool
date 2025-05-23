
import ttkbootstrap as tb
import psutil
from ui_layout import create_layout
from graph_manager import GraphManager
from iperf_test import IperfTest
from flood_ping_test import FloodPingTest
from utils import export_log, save_graph
from l2_traffic_test import L2TrafficTest
import time
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

def main():
    app = tb.Window(themename="flatly")
    app.title("Smart Throughput & Flood Ping Tester")
    app.attributes('-zoomed', True)  # Works on many Linux window managers
    interfaces = list(psutil.net_io_counters(pernic=True).keys())
    ui = create_layout(app, interfaces)
    graph = GraphManager(ui['graph_frames'])

    data_store = {
        'tx': [], 'rx': [], 'latency': [], 'timestamp': [], 'throughput': []
    }

    test_runner = {'instance': None}
    test_start_time = {'time': None}

    def enable_export_buttons():
        ui['export_log_btn'].config(state="normal")
        ui['save_tp_graph_btn'].config(state="normal")
        ui['save_latency_graph_btn'].config(state="normal")

    def update_metrics(tx, rx, latency, loss, duration_secs, total):
        ui['metrics_labels']['tx'].config(text=f"Tx: {tx:.2f} Mbps")
        ui['metrics_labels']['rx'].config(text=f"Rx: {rx:.2f} Mbps")
        ui['metrics_labels']['latency'].config(text=f"Latency: {latency:.2f} ms")
        ui['metrics_labels']['loss'].config(text=f"Loss: {loss:.2f}%")
        ui['metrics_labels']['duration'].config(text=f"Duration: {duration_secs // 60:02}:{duration_secs % 60:02}")
        ui['metrics_labels']['total'].config(text=f"Total: {total:.2f} Mbps")

    def update_visibility(*args):
        traffic_type = ui['traffic_var'].get()
        if traffic_type == "Flood Ping":
            ui['packet_size_label'].grid()
            ui['packet_size_entry'].grid()
            ui['protocol_label'].grid_remove()
            ui['protocol_menu'].grid_remove()
            ui['direction_label'].grid_remove()
            ui['direction_menu'].grid_remove()
            ui['entries']["Latency Threshold (ms)"].grid()
            ui['latency_label'].grid()
            ui['entries']["Remote MAC"].grid_remove()
        elif traffic_type == "L2/L3 Traffic":
            # Hide other irrelevant options
            ui['packet_size_label'].grid()
            ui['packet_size_entry'].grid()
            ui['protocol_label'].grid_remove()
            ui['protocol_menu'].grid_remove()
            ui['direction_label'].grid_remove()
            ui['direction_menu'].grid_remove()
            ui['entries']["Latency Threshold (ms)"].grid_remove()
            ui['latency_label'].grid_remove()
            # Show EtherType selector
            ui['ethertype_label'].grid()
            ui['ethertype_menu'].grid()
            ui['entries']["Remote MAC"].grid()
        else:
            ui['packet_size_label'].grid()
            ui['packet_size_entry'].grid()
            ui['protocol_label'].grid()
            ui['protocol_menu'].grid()
            ui['direction_label'].grid()
            ui['direction_menu'].grid()
            ui['entries']["Latency Threshold (ms)"].grid_remove()
            ui['latency_label'].grid_remove()
            ui['ethertype_label'].grid_remove()
            ui['ethertype_menu'].grid_remove()
            ui['entries']["Remote MAC"].grid_remove()

    def start_test():
        ui['output_area'].config(state='normal')
        ui['output_area'].delete('1.0', 'end')
        ui['output_area'].config(state='disabled')

        ui['export_log_btn'].config(state="disabled")
        ui['save_tp_graph_btn'].config(state="disabled")
        ui['save_latency_graph_btn'].config(state="disabled")

        remote_ip = ui['entries']['Remote IP'].get()
        iface = ui['iface_var'].get()
        profile = ui['profile_var'].get()
        traffic = ui['traffic_var'].get()
        protocol = ui['protocol_var'].get()
        direction = ui['direction_var'].get()
        packet_size = int(ui['packet_size_var'].get())
        latency_thresh = float(ui['entries']['Latency Threshold (ms)'].get())
        loss_thresh = float(ui['entries']['Loss Threshold (%)'].get())
        remote_mac = ui['entries']["Remote MAC"].get()
        ethertype = ui['ethertype_var'].get()

        ui_refs = {
            'output_area': ui['output_area'],
            'start_button': ui['start_button'],
            'stop_button': ui['stop_button'],
            'status_bar': ui['status_bar'],
            'export_log_btn': ui['export_log_btn'],
            'save_tp_graph_btn': ui['save_tp_graph_btn'],
            'save_latency_graph_btn': ui['save_latency_graph_btn'],
            'entries': ui['entries'],  # ← ADD THIS
            'update_metrics': update_metrics
        }

        for key in data_store:
            data_store[key].clear()
        graph.update_graphs(data_store['timestamp'], data_store['tx'], data_store['rx'], data_store['latency'])

        ui['start_button'].config(state='disabled')
        ui['stop_button'].config(state='normal')
        ui['status_bar'].config(text="Test Running...")

        test_start_time['time'] = time.time()

        if traffic == "Flood Ping":
            tester = FloodPingTest(remote_ip, iface, packet_size, profile,
                                   latency_thresh, loss_thresh, ui_refs, data_store, graph,
                                   update_metrics=update_metrics)
        elif traffic == "L2/L3 Traffic":
            tester = L2TrafficTest(
                iface=iface,
                ui_refs=ui_refs,
                ethertype=ui['ethertype_var'].get(),
                remote_mac=ui['entries']['Remote MAC'].get(),
                packet_size=packet_size,
                profile=profile,
                data_store=data_store,
                graph=graph,
                update_metrics=update_metrics
            )

        else:
            tester = IperfTest(
                remote_ip,
                iface,
                protocol,
                packet_size,
                direction,
                profile,
                ui_refs,
                data_store,
                graph,
                update_metrics=update_metrics,
                verbose_logging=False
            )

        test_runner['instance'] = tester
        ui['stop_button'].config(command=test_runner['instance'].stop)
        tester.run()

    ui['traffic_var'].trace_add("write", update_visibility)
    update_visibility()

    ui['start_button'].config(command=start_test)
    ui['export_log_btn'].config(command=lambda: export_log(ui['output_area']))
    ui['save_tp_graph_btn'].config(command=lambda: save_graph(graph.fig1, "throughput_graph"))
    ui['save_latency_graph_btn'].config(command=lambda: save_graph(graph.fig2, "latency_graph"))

    app.mainloop()

if __name__ == "__main__":
    main()