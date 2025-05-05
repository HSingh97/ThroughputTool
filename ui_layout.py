
import tkinter as tk
from tkinter import ttk

def create_layout(app, interfaces):
    app.title("Smart Throughput & Flood Ping Tester")
    app.attributes("-zoomed", True)

    main_frame = ttk.Frame(app, padding=10)
    main_frame.pack(fill="both", expand=True)

    paned = ttk.PanedWindow(main_frame, orient="horizontal")
    paned.pack(fill="both", expand=True)

    settings_frame = ttk.LabelFrame(paned, text="Settings", padding=15)
    paned.add(settings_frame, weight=1)

    output_frame = ttk.Frame(paned)
    paned.add(output_frame, weight=3)

    settings_frame.rowconfigure(4, weight=1)
    settings_frame.columnconfigure(0, weight=1)
    settings_frame.columnconfigure(1, weight=1)

    left_form = ttk.Frame(settings_frame)
    left_form.grid(row=0, column=0, sticky="nw", padx=5)

    right_form = ttk.Frame(settings_frame)
    right_form.grid(row=0, column=1, sticky="ne", padx=30)

    entries = {}

    fields = [
        ("Remote IP", "192.168.1.11"),
        ("Loss Threshold (%)", "10.0"),
        ("Latency Threshold (ms)", "70")
    ]

    latency_label = None
    for i, (label_text, default) in enumerate(fields):
        label = ttk.Label(left_form, text=label_text)
        label.grid(row=i, column=0, sticky="w", pady=2)
        entry = ttk.Entry(left_form)
        entry.insert(0, str(default))
        entry.grid(row=i, column=1, pady=2)
        entries[label_text] = entry
        if label_text == "Latency Threshold (ms)":
            latency_label = label

    iface_var = tk.StringVar(value=interfaces[0])
    profile_var = tk.StringVar(value="Moderate")
    traffic_var = tk.StringVar(value="iperf3")
    protocol_var = tk.StringVar(value="UDP")
    direction_var = tk.StringVar(value="Uplink")
    packet_size_var = tk.StringVar(value="1400")

    def combo(row, label, var, values, parent):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        widget = ttk.Combobox(parent, textvariable=var, values=values, state="readonly")
        widget.grid(row=row, column=1, pady=2)
        return widget

    combo(0, "Select Interface", iface_var, interfaces, right_form)
    combo(1, "Speed Profile", profile_var, ["Safe", "Moderate", "Aggressive"], right_form)
    combo(2, "Traffic Type", traffic_var, ["iperf3", "Flood Ping"], right_form)

    packet_label = ttk.Label(right_form, text="Packet Size (Flood Ping)")
    packet_entry = ttk.Entry(right_form, textvariable=packet_size_var)
    protocol_label = ttk.Label(right_form, text="Traffic Protocol Type")
    protocol_menu = ttk.Combobox(right_form, textvariable=protocol_var, values=["UDP", "TCP"], state="readonly")
    direction_label = ttk.Label(right_form, text="Traffic Direction")
    direction_menu = ttk.Combobox(right_form, textvariable=direction_var, values=["Uplink", "Downlink", "Bi-Di"], state="readonly")

    packet_label.grid(row=3, column=0, sticky="w", pady=2)
    packet_entry.grid(row=3, column=1, pady=2)
    protocol_label.grid(row=4, column=0, sticky="w", pady=2)
    protocol_menu.grid(row=4, column=1, pady=2)
    direction_label.grid(row=5, column=0, sticky="w", pady=2)
    direction_menu.grid(row=5, column=1, pady=2)

    def update_visibility(*args):
        if traffic_var.get() == "Flood Ping":
            packet_label.grid()
            packet_entry.grid()
            protocol_label.grid_remove()
            protocol_menu.grid_remove()
            direction_label.grid_remove()
            direction_menu.grid_remove()
            entries["Latency Threshold (ms)"].grid()
            latency_label.grid()
        else:
            packet_label.grid_remove()
            packet_entry.grid_remove()
            protocol_label.grid()
            protocol_menu.grid()
            direction_label.grid()
            direction_menu.grid()

            entries["Latency Threshold (ms)"].grid_remove()
            latency_label.grid_remove()

            # Update direction menu based on selected protocol
            if protocol_var.get() == "UDP":
                direction_menu['values'] = ["Uplink", "Downlink"]
                if direction_var.get() == "Bi-Di":
                    direction_var.set("Uplink")  # fallback
            else:
                direction_menu['values'] = ["Uplink", "Downlink", "Bi-Di"]

    traffic_var.trace_add("write", update_visibility)
    protocol_var.trace_add("write", update_visibility)
    update_visibility()

    button_frame = ttk.Frame(settings_frame)
    button_frame.grid(row=1, column=0, columnspan=2, pady=10)

    start_button = ttk.Button(button_frame, text="Start Test")
    stop_button = ttk.Button(button_frame, text="Stop Test", state="disabled")
    start_button.grid(row=0, column=0, padx=5)
    stop_button.grid(row=0, column=1, padx=5)

    #link_label = ttk.Label(settings_frame, text="Live Tx: 0.00 Mbps | Rx: 0.00 Mbps", font=("Segoe UI", 10))
    #link_label.grid(row=2, column=0, columnspan=2, sticky="w", pady=5)

    metrics_frame = ttk.LabelFrame(settings_frame, text="Live Metrics", padding=(10, 5))
    metrics_frame.grid(row=3, column=0, columnspan=2, sticky="we", pady=5)

    metrics_labels = {
        "latency": ttk.Label(metrics_frame, text="Latency: N/A"),
        "tx": ttk.Label(metrics_frame, text="Tx: N/A"),
        "rx": ttk.Label(metrics_frame, text="Rx: N/A"),
        "total": ttk.Label(metrics_frame, text="Total: N/A"),
        "loss": ttk.Label(metrics_frame, text="Loss: N/A"),
        "duration": ttk.Label(metrics_frame, text="Duration: 00:00")
    }

    for i, label in enumerate(metrics_labels.values()):
        label.grid(row=0, column=i, padx=8)

    output_area = tk.Text(settings_frame, wrap="word")
    output_area.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)

    graph_container = ttk.Frame(output_frame)
    graph_container.pack(fill="both", expand=True)

    tp_frame = ttk.Frame(graph_container)
    latency_frame = ttk.Frame(graph_container)
    tp_frame.pack(fill="both", expand=True)
    latency_frame.pack(fill="both", expand=True)

    export_frame = ttk.Frame(output_frame)
    export_frame.pack(pady=5)

    export_log_btn = ttk.Button(export_frame, text="Export Log", state="disabled")
    save_tp_graph_btn = ttk.Button(export_frame, text="Save Throughput Graph", state="disabled")
    save_latency_graph_btn = ttk.Button(export_frame, text="Save Latency Graph", state="disabled")

    export_log_btn.pack(side="left", padx=5)
    save_tp_graph_btn.pack(side="left", padx=5)
    save_latency_graph_btn.pack(side="left", padx=5)

    status_bar = ttk.Label(app, text="Ready", anchor="w")
    status_bar.pack(side="bottom", fill="x")

    return {
        "entries": entries,
        "latency_label": latency_label,
        "iface_var": iface_var,
        "profile_var": profile_var,
        "traffic_var": traffic_var,
        "protocol_var": protocol_var,
        "direction_var": direction_var,
        "packet_size_var": packet_size_var,
        "packet_size_entry": packet_entry,
        "packet_size_label": packet_label,
        "protocol_label": protocol_label,
        "direction_label": direction_label,
        "protocol_menu": protocol_menu,
        "direction_menu": direction_menu,
        "output_area": output_area,
        "graph_frames": {
            "throughput": tp_frame,
            "latency": latency_frame
        },
        "start_button": start_button,
        "stop_button": stop_button,
        "export_log_btn": export_log_btn,
        "save_tp_graph_btn": save_tp_graph_btn,
        "save_latency_graph_btn": save_latency_graph_btn,
        "status_bar": status_bar,
        "metrics_labels": metrics_labels,
    }