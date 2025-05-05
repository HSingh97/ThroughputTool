# graph_manager.py

import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

class GraphManager:
    def __init__(self, graph_frames):
        self.fig1, self.ax1 = plt.subplots(figsize=(7, 3))
        self.fig2, self.ax2 = plt.subplots(figsize=(7, 3))

        self.canvas1 = FigureCanvasTkAgg(self.fig1, master=graph_frames['throughput'])
        self.canvas2 = FigureCanvasTkAgg(self.fig2, master=graph_frames['latency'])

        self.canvas1.get_tk_widget().pack(fill="both", expand=True)
        self.canvas2.get_tk_widget().pack(fill="both", expand=True)

        self.fig1.tight_layout()
        self.fig2.tight_layout()

    def update_graphs(self, timestamps, tx_data, rx_data, latency_data):
        self.ax1.clear()
        self.ax2.clear()

        if not timestamps:
            return

        rel_time = [t - timestamps[0] for t in timestamps]

        if tx_data and rx_data:
            tx_trim = tx_data[:len(rel_time)]
            rx_trim = rx_data[:len(rel_time)]
            total = [tx + rx for tx, rx in zip(tx_trim, rx_trim)]
            self.ax1.plot(rel_time, tx_trim, label="Tx")
            self.ax1.plot(rel_time, rx_trim, label="Rx")
            self.ax1.plot(rel_time, total, label="Total")
            self.ax1.set_ylabel("Throughput (Mbps)")
            self.ax1.set_title("Throughput vs Time")
            self.ax1.grid(True)
            self.ax1.legend()
            self.fig1.tight_layout()

        if latency_data:
            latency_trim = latency_data[:len(rel_time)]
            self.ax2.plot(rel_time[:len(latency_trim)], latency_trim, label="Latency", color="red")
            self.ax2.set_ylabel("Latency (ms)")
            self.ax2.set_title("Latency vs Time")
            self.ax2.grid(True)
            self.ax2.legend()
            self.fig2.tight_layout()

        self.canvas1.draw()
        self.canvas2.draw()

    @property
    def fig1(self):
        return self._fig1

    @property
    def fig2(self):
        return self._fig2

    @fig1.setter
    def fig1(self, fig):
        self._fig1 = fig

    @fig2.setter
    def fig2(self, fig):
        self._fig2 = fig
