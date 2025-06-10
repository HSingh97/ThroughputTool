# ui_layout.py

import tkinter as tk
from tkinter import ttk, scrolledtext
from PIL import Image, ImageTk


def create_layout(app, interfaces):
    app.title("Throughput & Flood Ping Tester - 1.1.0")
    app.attributes("-zoomed", True)

    main_frame = ttk.Frame(app, padding=10)
    main_frame.pack(fill="both", expand=True)

    paned = ttk.PanedWindow(main_frame, orient="horizontal")
    paned.pack(fill="both", expand=True)

    settings_frame = ttk.LabelFrame(paned, text="Settings", padding=15, bootstyle="info")
    paned.add(settings_frame, weight=1)

    output_frame = ttk.Frame(paned)
    paned.add(output_frame, weight=3)

    settings_frame.columnconfigure(0, weight=1)

    # --- 1. Control Panel ---
    control_panel_frame = ttk.LabelFrame(settings_frame, text="Control Panel", padding=10)
    control_panel_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
    control_panel_frame.columnconfigure((0, 1), weight=1)

    try:
        start_icon = ImageTk.PhotoImage(Image.open("start_icon.png").resize((16, 16), Image.Resampling.LANCZOS))
        stop_icon = ImageTk.PhotoImage(Image.open("stop_icon.png").resize((16, 16), Image.Resampling.LANCZOS))
    except (FileNotFoundError, NameError):
        print("Warning: Icon files (start_icon.png, stop_icon.png) not found or Pillow not installed. Buttons will be text-only.")
        start_icon = stop_icon = None

    start_button = ttk.Button(control_panel_frame, text=" Start Test", image=start_icon, compound="left",
                              bootstyle="success")
    stop_button = ttk.Button(control_panel_frame, text=" Stop Test", image=stop_icon, compound="left", state="disabled",
                             bootstyle="danger")
    start_button.grid(row=0, column=0, padx=5, pady=5, sticky="ew", ipady=5)
    stop_button.grid(row=0, column=1, padx=5, pady=5, sticky="ew", ipady=5)

    # --- 2. Main Configuration ---
    config_frame = ttk.Frame(settings_frame)
    config_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 10))
    config_frame.columnconfigure((0, 2), weight=1) # 3 columns for left, separator, right

    left_form = ttk.Frame(config_frame)
    left_form.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
    left_form.columnconfigure(1, weight=1)

    ttk.Separator(config_frame, orient='vertical').grid(row=0, column=1, sticky='ns', padx=5)

    right_form = ttk.Frame(config_frame)
    right_form.grid(row=0, column=2, sticky="nsew", padx=(10, 0))
    right_form.columnconfigure(1, weight=1)

    entries = {}
    ui_widgets = {}

    # --- Left Form Fields ---
    fields_left = [("Remote IP*", "192.168.1.11"), ("Loss Threshold (%)", "10.0"), ("Latency Threshold (ms)", "70"),
                   ("Remote MAC", "98:ba:5f:a9:7b:72"), ("Target L2 Rate (Mbps)", "50.0")]

    current_row_left = 0
    for label_text, default in fields_left:
        clean_label_text = label_text.replace("*", "")
        lbl = ttk.Label(left_form, text=clean_label_text)
        lbl.grid(row=current_row_left, column=0, sticky="w", pady=3)
        entry = ttk.Entry(left_form)
        entry.insert(0, str(default))
        entry.grid(row=current_row_left, column=1, pady=3, sticky="ew")
        entries[clean_label_text] = entry
        ui_widgets[clean_label_text + "_label"] = lbl
        ui_widgets[clean_label_text + "_entry"] = entry
        current_row_left += 1

    ssh_details_frame = ttk.LabelFrame(left_form, text="Remote Rx (L2/L3 via SSH)")
    ui_widgets["ssh_details_frame"] = ssh_details_frame
    entries["Remote Username"] = ttk.Entry(ssh_details_frame)
    entries["Remote Password"] = ttk.Entry(ssh_details_frame, show="*")
    entries["Remote Interface"] = ttk.Entry(ssh_details_frame)
    ttk.Label(ssh_details_frame, text="Username").grid(row=0, column=0, sticky="w", pady=2, padx=5)
    entries["Remote Username"].grid(row=0, column=1, pady=2, padx=5, sticky="ew")
    entries["Remote Username"].insert(0, "root")
    ttk.Label(ssh_details_frame, text="Password").grid(row=1, column=0, sticky="w", pady=2, padx=5)
    entries["Remote Password"].grid(row=1, column=1, pady=2, padx=5, sticky="ew")
    entries["Remote Password"].insert(0, "senao1234#")
    ttk.Label(ssh_details_frame, text="Interface").grid(row=2, column=0, sticky="w", pady=2, padx=5)
    entries["Remote Interface"].grid(row=2, column=1, pady=2, padx=5, sticky="ew")
    entries["Remote Interface"].insert(0, "enp1s0")
    ssh_details_frame.columnconfigure(1, weight=1)

    # --- Right Form Fields ---
    iface_var = tk.StringVar(value=interfaces[0] if interfaces else "")
    profile_var = tk.StringVar(value="Moderate")
    traffic_var = tk.StringVar(value="L2/L3 Traffic")
    protocol_var = tk.StringVar(value="UDP")
    direction_var = tk.StringVar(value="Uplink")
    packet_size_var = tk.StringVar(value="1400")
    ethertype_var = tk.StringVar(value="MPLS")

    def create_combo(parent, label_text, var, values, row_idx):
        lbl = ttk.Label(parent, text=label_text)
        combo = ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=18)
        ui_widgets[label_text + "_label"] = lbl
        ui_widgets[label_text + "_combo"] = combo
        lbl.grid(row=row_idx, column=0, sticky="w", pady=3)
        combo.grid(row=row_idx, column=1, pady=3, sticky="ew")
        return combo

    create_combo(right_form, "Select Interface", iface_var, interfaces, 0)
    create_combo(right_form, "Traffic Type", traffic_var, ["iperf3", "Flood Ping", "L2/L3 Traffic"], 1)
    ui_widgets["Speed Profile_label"] = ttk.Label(right_form, text="Speed Profile")
    ui_widgets["Speed Profile_combo"] = ttk.Combobox(right_form, textvariable=profile_var, values=["Safe", "Moderate", "Aggressive"], state="readonly", width=18)
    ui_widgets["Packet Size_label"] = ttk.Label(right_form, text="Packet Size")
    ui_widgets["Packet Size_entry"] = ttk.Entry(right_form, textvariable=packet_size_var, width=21)
    create_combo(right_form, "Protocol (iperf3)", protocol_var, ["UDP", "TCP"], 4)
    create_combo(right_form, "Direction (iperf3)", direction_var, ["Uplink", "Downlink", "Bi-Di"], 5)
    create_combo(right_form, "EtherType (L2/L3)", ethertype_var, ["IPv4", "ARP", "IPv6", "VLAN", "MPLS", "PPPoE", "Loopback", "Unknown", "0xFFFF"], 6)

    # --- 3. Live Metrics ---
    metrics_frame = ttk.LabelFrame(settings_frame, text="Live Metrics", padding=(10, 5))
    metrics_frame.grid(row=2, column=0, sticky="ew", pady=(10, 5))
    metrics_frame.columnconfigure((0, 1, 2, 3), weight=1)
    metrics_labels = {"duration": ttk.Label(metrics_frame, text="Duration: N/A"), "latency": ttk.Label(metrics_frame, text="Latency: N/A"),
                      "loss": ttk.Label(metrics_frame, text="Loss: N/A%", bootstyle="success"), "local_tx": ttk.Label(metrics_frame, text="Local Tx: N/A"),
                      "local_rx": ttk.Label(metrics_frame, text="Local Rx: N/A"), "remote_rx": ttk.Label(metrics_frame, text="Remote Rx: N/A"),
                      "total": ttk.Label(metrics_frame, text="Total Bw: N/A")}
    metrics_labels["duration"].grid(row=0, column=0, padx=5, pady=2, sticky="w")
    metrics_labels["latency"].grid(row=0, column=1, padx=5, pady=2, sticky="w")
    metrics_labels["loss"].grid(row=0, column=2, padx=5, pady=2, sticky="w")
    metrics_labels["local_tx"].grid(row=1, column=0, padx=5, pady=2, sticky="w")
    metrics_labels["local_rx"].grid(row=1, column=1, padx=5, pady=2, sticky="w")
    metrics_labels["total"].grid(row=1, column=3, padx=5, pady=2, sticky="w")

    def update_visibility(*args):
        # This function remains complex but its logic is unchanged
        traffic = traffic_var.get()
        protocol_sel = protocol_var.get()
        def set_visibility(widget_key_base, new_row=None, visible=True, parent=right_form, col=0, entry_col=1, sticky_lbl="w", sticky_wdgt="ew", p_text=None, columnspan_wdgt=1):
            lbl = ui_widgets.get(widget_key_base + "_label")
            wdgt = ui_widgets.get(widget_key_base + "_combo") or ui_widgets.get(widget_key_base + "_entry") or ui_widgets.get(widget_key_base + "_frame")
            if p_text and lbl: lbl.config(text=p_text)
            if visible and new_row is not None:
                if lbl: lbl.grid(row=new_row, column=col, sticky=sticky_lbl, pady=3, padx=(5 if parent == ssh_details_frame else 0))
                if wdgt: wdgt.grid(row=new_row, column=(entry_col if lbl else col), pady=3, sticky=sticky_wdgt, columnspan=columnspan_wdgt)
            else:
                if lbl: lbl.grid_remove()
                if wdgt: wdgt.grid_remove()
        set_visibility("Speed Profile", visible=False); set_visibility("Protocol (iperf3)", visible=False); set_visibility("Direction (iperf3)", visible=False)
        set_visibility("EtherType (L2/L3)", visible=False); set_visibility("Packet Size", visible=False); set_visibility("Latency Threshold (ms)", parent=left_form, visible=False)
        set_visibility("Remote MAC", parent=left_form, visible=False); set_visibility("Target L2 Rate (Mbps)", parent=left_form, visible=False)
        set_visibility("ssh_details", parent=left_form, visible=False)
        if metrics_labels.get("remote_rx"):
            if traffic == "L2/L3 Traffic": metrics_labels["remote_rx"].grid(row=1, column=2, padx=5, pady=2, sticky="w")
            else: metrics_labels["remote_rx"].grid_remove()
        row_idx_right = 2
        if traffic == "iperf3":
            set_visibility("Protocol (iperf3)", new_row=row_idx_right, visible=True); row_idx_right += 1
            set_visibility("Direction (iperf3)", new_row=row_idx_right, visible=True); row_idx_right += 1
            if protocol_sel == "UDP": set_visibility("Speed Profile", new_row=row_idx_right, visible=True); row_idx_right += 1
            set_visibility("Packet Size", new_row=row_idx_right, p_text="Block/Length Size (iperf3)", visible=True); row_idx_right += 1
            if ui_widgets["Direction (iperf3)_combo"].cget('values') != ("Uplink", "Downlink", "Bi-Di"): ui_widgets["Direction (iperf3)_combo"]['values'] = ("Uplink", "Downlink", "Bi-Di")
        elif traffic == "Flood Ping":
            set_visibility("Speed Profile", new_row=row_idx_right, visible=True); row_idx_right += 1
            set_visibility("Packet Size", new_row=row_idx_right, p_text="Packet Size (Flood Ping)", visible=True); row_idx_right += 1
            set_visibility("Latency Threshold (ms)", new_row=2, parent=left_form, visible=True)
        elif traffic == "L2/L3 Traffic":
            set_visibility("Speed Profile", new_row=row_idx_right, visible=True); row_idx_right += 1
            set_visibility("EtherType (L2/L3)", new_row=row_idx_right, visible=True); row_idx_right += 1
            set_visibility("Packet Size", new_row=row_idx_right, p_text="Packet Size (L2/L3)", visible=True); row_idx_right += 1
            set_visibility("Remote MAC", new_row=3, parent=left_form, visible=True)
            set_visibility("Target L2 Rate (Mbps)", new_row=4, parent=left_form, visible=True)
            ssh_frame_row = current_row_left
            set_visibility("ssh_details", new_row=ssh_frame_row, parent=left_form, visible=True, col=0, columnspan_wdgt=2)
            left_form.grid_rowconfigure(ssh_frame_row, weight=1)
        loss_entry = entries.get("Loss Threshold (%)")
        loss_label_widget = ui_widgets.get("Loss Threshold (%)_label")
        if loss_entry and loss_label_widget:
            loss_visible = traffic in ["iperf3", "Flood Ping"]
            set_visibility("Loss Threshold (%)", new_row=1, parent=left_form, visible=loss_visible)
            if loss_visible:
                if traffic == "iperf3":
                    if protocol_sel == "UDP" and loss_entry.get() != "1.0": loss_entry.delete(0, tk.END); loss_entry.insert(0, "1.0")
                    elif protocol_sel == "TCP" and loss_entry.get() != "10.0": loss_entry.delete(0, tk.END); loss_entry.insert(0, "10.0")
                elif traffic == "Flood Ping":
                    if loss_entry.get() != "10.0": loss_entry.delete(0, tk.END); loss_entry.insert(0, "10.0")

    traffic_var.trace_add("write", update_visibility)
    protocol_var.trace_add("write", update_visibility)
    update_visibility()

    # --- 4. Output Area & Graphs ---
    monospace_font = ("Courier New", 10)
    output_area = scrolledtext.ScrolledText(settings_frame, wrap="none", font=monospace_font, height=10)
    output_area.grid(row=4, column=0, sticky="nsew", pady=(10, 0)) # Moved to row 4
    settings_frame.rowconfigure(4, weight=1)

    graph_container = ttk.Frame(output_frame)
    graph_container.pack(fill="both", expand=True, padx=5, pady=0)
    tp_frame = ttk.LabelFrame(graph_container, text="Throughput Graph")
    latency_frame = ttk.LabelFrame(graph_container, text="Latency Graph")
    tp_frame.pack(side="top", fill="both", expand=True, pady=(0, 5))
    latency_frame.pack(side="top", fill="both", expand=True, pady=(5, 0))

    export_frame = ttk.Frame(output_frame)
    export_frame.pack(fill="x", pady=5, padx=5)
    export_log_btn = ttk.Button(export_frame, text="Export Log", state="disabled", bootstyle="secondary-outline")
    save_tp_graph_btn = ttk.Button(export_frame, text="Save Throughput Graph", state="disabled", bootstyle="secondary-outline")
    save_latency_graph_btn = ttk.Button(export_frame, text="Save Latency Graph", state="disabled", bootstyle="secondary-outline")
    export_log_btn.pack(side="left", padx=5, expand=True, fill="x")
    save_tp_graph_btn.pack(side="left", padx=5, expand=True, fill="x")
    save_latency_graph_btn.pack(side="left", padx=5, expand=True, fill="x")

    status_bar = ttk.Label(app, text="Ready", relief="sunken", anchor="w", bootstyle="primary-inverse", padding=(10, 5))
    status_bar.pack(side="bottom", fill="x", pady=(2, 0))

    return {
        "entries": entries, "iface_var": iface_var, "profile_var": profile_var, "traffic_var": traffic_var,
        "protocol_var": protocol_var, "direction_var": direction_var, "packet_size_var": packet_size_var,
        "ethertype_var": ethertype_var, "output_area": output_area,
        "graph_frames": {"throughput": tp_frame, "latency": latency_frame},
        "start_button": start_button, "stop_button": stop_button,
        "export_log_btn": export_log_btn, "save_tp_graph_btn": save_tp_graph_btn,
        "save_latency_graph_btn": save_latency_graph_btn, "status_bar": status_bar, "metrics_labels": metrics_labels,
        # Keep other widget refs as they are used by update_visibility
        "packet_size_entry": ui_widgets["Packet Size_entry"], "packet_size_label": ui_widgets["Packet Size_label"],
        "latency_label": ui_widgets.get("Latency Threshold (ms)_label"), "remote_mac_label": ui_widgets.get("Remote MAC_label"),
        "ethertype_label": ui_widgets["EtherType (L2/L3)_label"], "ethertype_menu": ui_widgets["EtherType (L2/L3)_combo"],
        "protocol_label": ui_widgets["Protocol (iperf3)_label"], "protocol_menu": ui_widgets["Protocol (iperf3)_combo"],
        "direction_label": ui_widgets["Direction (iperf3)_label"], "direction_menu": ui_widgets["Direction (iperf3)_combo"],
        "speed_profile_label": ui_widgets["Speed Profile_label"], "speed_profile_combo": ui_widgets["Speed Profile_combo"]
    }