# graph_manager.py

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Use a style suitable for light themes like 'flatly'
plt.style.use('seaborn-v0_8-whitegrid')


class GraphManager:
    def __init__(self, graph_frames):
        # The default white facecolor of the style will match the 'flatly' theme.
        self.fig1, self.ax1 = plt.subplots(figsize=(7, 3))
        self.fig2, self.ax2 = plt.subplots(figsize=(7, 3))

        self.canvas1 = FigureCanvasTkAgg(self.fig1, master=graph_frames['throughput'])
        self.canvas2 = FigureCanvasTkAgg(self.fig2, master=graph_frames['latency'])

        self.canvas1.get_tk_widget().pack(fill="both", expand=True)
        self.canvas2.get_tk_widget().pack(fill="both", expand=True)

    def update_graphs(self, timestamps, tx_data, rx_data, latency_data):
        self.ax1.clear()
        self.ax2.clear()

        # Set titles and labels every time to ensure they persist
        self.ax1.set_title("Throughput vs Time")
        self.ax1.set_ylabel("Throughput (Mbps)")
        self.ax1.set_xlabel("Time (seconds)")

        self.ax2.set_title("Latency vs Time")
        self.ax2.set_ylabel("Latency (ms)")
        self.ax2.set_xlabel("Time (seconds)")

        if timestamps:
            rel_time = [t - timestamps[0] for t in timestamps]

            # Plot Throughput Data
            if tx_data or rx_data:  # Plot even if only one is present
                tx_trim = tx_data[:len(rel_time)] if tx_data else [0] * len(rel_time)
                rx_trim = rx_data[:len(rel_time)] if rx_data else [0] * len(rel_time)
                total = [tx + rx for tx, rx in zip(tx_trim, rx_trim)]

                self.ax1.plot(rel_time, tx_trim, label="Local Tx", marker='.', linestyle='-', markersize=4)
                self.ax1.plot(rel_time, rx_trim, label="Remote Rx", marker='.', linestyle='-', markersize=4)
                self.ax1.plot(rel_time, total, label="Total", linestyle='--')
                self.ax1.legend(loc='upper left')

            # Plot Latency Data
            if latency_data:
                latency_trim = latency_data[:len(rel_time)]
                self.ax2.plot(rel_time[:len(latency_trim)], latency_trim, label="Latency", color="orangered",
                              marker='.', linestyle='-', markersize=4)
                self.ax2.legend(loc='upper left')

        # Apply tight_layout after plotting and draw
        self.fig1.tight_layout()
        self.fig2.tight_layout()
        self.canvas1.draw()
        self.canvas2.draw()

    # Define properties to allow main.py to access the figures for saving
    @property
    def fig1_prop(self):
        return self.fig1

    @property
    def fig2_prop(self):
        return self.fig2