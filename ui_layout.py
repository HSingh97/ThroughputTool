import tkinter as tk
from tkinter import ttk
from tkinter import scrolledtext  # Import the scrolledtext module


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

    # MODIFICATION: Set initial default for Loss Threshold to 1.0%
    # This aligns with UDP being the default iperf3 protocol.
    # update_visibility will handle changes if TCP is selected.
    fields = [
        ("Remote IP", "192.168.1.11"),
        ("Loss Threshold (%)", "10.0"),  # Initial default for the GUI
        ("Latency Threshold (ms)", "70"),
        ("Remote MAC", "98:ba:5f:a9:7b:72")
    ]

    latency_label = None
    remote_mac_label = None
    for i, (label_text, default) in enumerate(fields):
        label = ttk.Label(left_form, text=label_text)
        label.grid(row=i, column=0, sticky="w", pady=2)
        entry = ttk.Entry(left_form)
        entry.insert(0, str(default))
        entry.grid(row=i, column=1, pady=2)
        entries[label_text] = entry
        if label_text == "Latency Threshold (ms)":
            latency_label = label
        if label_text == "Remote MAC":
            remote_mac_label = label

    # Default selections for protocol and traffic type
    iface_var = tk.StringVar(value=interfaces[0] if interfaces else "")
    profile_var = tk.StringVar(value="Moderate")
    traffic_var = tk.StringVar(value="iperf3")  # Default traffic type
    protocol_var = tk.StringVar(value="UDP")  # Default protocol for iperf3
    direction_var = tk.StringVar(value="Uplink")
    packet_size_var = tk.StringVar(value="1400")
    ethertype_var = tk.StringVar(value="IPv4")

    speed_profile_label = ttk.Label(right_form, text="Speed Profile")
    speed_profile_combo = ttk.Combobox(
        right_form,
        textvariable=profile_var,
        values=["Safe", "Moderate", "Aggressive"],
        state="readonly"
    )
    # Speed profile gridding is handled in update_visibility initially

    ethertype_label = ttk.Label(right_form, text="EtherType")
    ethertype_menu = ttk.Combobox(
        right_form,
        textvariable=ethertype_var,
        values=["IPv4", "ARP", "IPv6", "VLAN", "MPLS", "PPPoE", "Loopback", "Unknown", "0xFFFF"],
        state="readonly"
    )

    def combo(row, label_text, var, values, parent):
        lbl = ttk.Label(parent, text=label_text)
        lbl.grid(row=row, column=0, sticky="w", pady=2)
        widget = ttk.Combobox(parent, textvariable=var, values=values, state="readonly")
        widget.grid(row=row, column=1, pady=2)
        return widget

    combo(0, "Select Interface", iface_var, interfaces, right_form)
    combo(1, "Traffic Type", traffic_var, ["iperf3", "Flood Ping", "L2/L3 Traffic"], right_form)

    # Speed Profile will be gridded at row 2 by update_visibility

    packet_label = ttk.Label(right_form, text="Packet Size")
    packet_entry = ttk.Entry(right_form, textvariable=packet_size_var)
    protocol_label = ttk.Label(right_form, text="Traffic Protocol Type")
    protocol_menu = ttk.Combobox(right_form, textvariable=protocol_var, values=["UDP", "TCP"], state="readonly")
    direction_label = ttk.Label(right_form, text="Traffic Direction")
    direction_menu = ttk.Combobox(right_form, textvariable=direction_var, values=["Uplink", "Downlink", "Bi-Di"],
                                  state="readonly")

    # Initial gridding for these, update_visibility will manage them
    packet_label.grid(row=3, column=0, sticky="w", pady=2)
    packet_entry.grid(row=3, column=1, pady=2)
    protocol_label.grid(row=4, column=0, sticky="w", pady=2)
    protocol_menu.grid(row=4, column=1, pady=2)
    direction_label.grid(row=5, column=0, sticky="w", pady=2)
    direction_menu.grid(row=5, column=1, pady=2)
    ethertype_label.grid(row=6, column=0, sticky="w", pady=2)
    ethertype_menu.grid(row=6, column=1, pady=2)

    def update_visibility(*args):
        traffic_type = traffic_var.get()
        selected_protocol = protocol_var.get()
        loss_threshold_entry = entries.get("Loss Threshold (%)")

        # --- Speed Profile Visibility ---
        if traffic_type == "iperf3" and selected_protocol == "TCP":
            speed_profile_label.grid_remove()
            speed_profile_combo.grid_remove()
        else:
            speed_profile_label.grid(row=2, column=0, sticky="w", pady=2)
            speed_profile_combo.grid(row=2, column=1, pady=2)

        # --- MODIFICATION: Dynamically set Loss Threshold default ---
        if loss_threshold_entry:
            if traffic_type == "iperf3":
                # Assuming Loss Threshold is always relevant and visible for iperf3
                if selected_protocol == "UDP":
                    # Only change if not already "1.0" to allow user edits if they typed something else temporarily
                    if loss_threshold_entry.get() != "1.0":
                        loss_threshold_entry.delete(0, tk.END)
                        loss_threshold_entry.insert(0, "1.0")
                elif selected_protocol == "TCP":
                    if loss_threshold_entry.get() != "10.0":
                        loss_threshold_entry.delete(0, tk.END)
                        loss_threshold_entry.insert(0, "10.0")
            elif traffic_type == "Flood Ping":
                if loss_threshold_entry.get() != "10.0":
                    loss_threshold_entry.delete(0, tk.END)
                    loss_threshold_entry.insert(0, "10.0")
                # else:
                # For non-iperf3 traffic types, the Loss Threshold field might be hidden by other logic.
                # If it were to remain visible for other types, you could set a default here too.
                # Example: if it's visible and not iperf3, set to "10.0"
                # elif loss_threshold_entry.winfo_ismapped() and loss_threshold_entry.get() != "10.0":
                # loss_threshold_entry.delete(0, tk.END)
                # loss_threshold_entry.insert(0, "10.0")
                pass  # Current logic primarily focuses on iperf3 for this field's dynamic default.

        if traffic_type == "Flood Ping":
            packet_label.config(text="Packet Size (Flood Ping)")
        elif traffic_type == "L2/L3 Traffic":
            packet_label.config(text="Packet Size (L2/L3)")
        else:  # iperf3
            packet_label.config(text="Block/Length Size (iperf3)")

        if traffic_type == "Flood Ping":
            packet_label.grid(row=3, column=0, sticky="w", pady=2)
            packet_entry.grid(row=3, column=1, pady=2)
            protocol_label.grid_remove();
            protocol_menu.grid_remove()
            direction_label.grid_remove();
            direction_menu.grid_remove()
            if "Latency Threshold (ms)" in entries and latency_label:
                entries["Latency Threshold (ms)"].grid(row=2, column=1, pady=2)  # Note: row 2 on left form
                latency_label.grid(row=2, column=0, sticky="w", pady=2)
            if "Remote MAC" in entries and remote_mac_label:
                entries["Remote MAC"].grid_remove();
                remote_mac_label.grid_remove()
            ethertype_label.grid_remove();
            ethertype_menu.grid_remove()
        elif traffic_type == "L2/L3 Traffic":
            packet_label.grid(row=3, column=0, sticky="w", pady=2)
            packet_entry.grid(row=3, column=1, pady=2)
            protocol_label.grid_remove();
            protocol_menu.grid_remove()
            direction_label.grid_remove();
            direction_menu.grid_remove()
            if "Latency Threshold (ms)" in entries and latency_label:
                entries["Latency Threshold (ms)"].grid_remove();
                latency_label.grid_remove()
            ethertype_label.grid(row=6, column=0, sticky="w", pady=2)  # Row 6 on right_form
            ethertype_menu.grid(row=6, column=1, pady=2)
            if "Remote MAC" in entries and remote_mac_label:
                entries["Remote MAC"].grid(row=3, column=1, pady=2)  # Note: row 3 on left_form
                remote_mac_label.grid(row=3, column=0, sticky="w", pady=2)
        else:  # iperf3
            packet_label.grid(row=3, column=0, sticky="w", pady=2)
            packet_entry.grid(row=3, column=1, pady=2)
            protocol_label.grid(row=4, column=0, sticky="w", pady=2)
            protocol_menu.grid(row=4, column=1, pady=2)
            direction_label.grid(row=5, column=0, sticky="w", pady=2)
            direction_menu.grid(row=5, column=1, pady=2)

            if "Latency Threshold (ms)" in entries and latency_label:
                entries["Latency Threshold (ms)"].grid_remove();
                latency_label.grid_remove()
            if "Remote MAC" in entries and remote_mac_label:
                entries["Remote MAC"].grid_remove();
                remote_mac_label.grid_remove()
            ethertype_label.grid_remove();
            ethertype_menu.grid_remove()

            if selected_protocol == "UDP":
                direction_menu['values'] = ["Uplink", "Downlink"]
                if direction_var.get() == "Bi-Di": direction_var.set("Uplink")
            else:  # TCP
                direction_menu['values'] = ["Uplink", "Downlink", "Bi-Di"]

    traffic_var.trace_add("write", update_visibility)
    protocol_var.trace_add("write", update_visibility)  # This trace will trigger the default update

    # Initial call to set visibility and default for Loss Threshold
    update_visibility()

    button_frame = ttk.Frame(settings_frame)
    button_frame.grid(row=1, column=0, columnspan=2, pady=10)

    start_button = ttk.Button(button_frame, text="Start Test")
    stop_button = ttk.Button(button_frame, text="Stop Test", state="disabled")
    start_button.grid(row=0, column=0, padx=5)
    stop_button.grid(row=0, column=1, padx=5)

    metrics_frame = ttk.LabelFrame(settings_frame, text="Live Metrics", padding=(10, 5))
    metrics_frame.grid(row=3, column=0, columnspan=2, sticky="ew", pady=5)
    metrics_labels = {
        "latency": ttk.Label(metrics_frame, text="Latency: N/A"),
        "tx": ttk.Label(metrics_frame, text="Tx: N/A"),
        "rx": ttk.Label(metrics_frame, text="Rx: N/A"),
        "total": ttk.Label(metrics_frame, text="Total: N/A"),
        "loss": ttk.Label(metrics_frame, text="Loss: N/A"),
        "duration": ttk.Label(metrics_frame, text="Duration: 00:00")
    }
    for i in range(len(metrics_labels)): metrics_frame.columnconfigure(i, weight=1)
    for i, label_widget in enumerate(metrics_labels.values()):
        label_widget.grid(row=0, column=i, padx=5, sticky="ew")

    monospace_font = ("Courier New", 10)
    output_area = scrolledtext.ScrolledText(settings_frame, wrap="none", font=monospace_font)
    output_area.grid(row=4, column=0, columnspan=2, sticky="nsew", padx=5, pady=5)

    graph_container = ttk.Frame(output_frame)
    graph_container.pack(fill="both", expand=True)
    tp_frame = ttk.Frame(graph_container);
    latency_frame = ttk.Frame(graph_container)
    tp_frame.pack(fill="both", expand=True);
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
        "remote_mac_label": remote_mac_label,
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
        "graph_frames": {"throughput": tp_frame, "latency": latency_frame},
        "start_button": start_button, "stop_button": stop_button,
        "export_log_btn": export_log_btn, "save_tp_graph_btn": save_tp_graph_btn,
        "save_latency_graph_btn": save_latency_graph_btn,
        "status_bar": status_bar, "metrics_labels": metrics_labels,
        "ethertype_var": ethertype_var, "ethertype_label": ethertype_label, "ethertype_menu": ethertype_menu,
        "speed_profile_label": speed_profile_label, "speed_profile_combo": speed_profile_combo,
    }