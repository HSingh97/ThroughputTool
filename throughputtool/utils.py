# utils.py

from tkinter import filedialog

def export_log(output_widget):
    file_path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text Files", "*.txt")])
    if file_path:
        with open(file_path, 'w') as f:
            f.write(output_widget.get('1.0', 'end'))


def save_graph(fig, default_name):
    file_path = filedialog.asksaveasfilename(defaultextension=".png", filetypes=[("PNG Image", "*.png")], initialfile=default_name)
    if file_path:
        fig.savefig(file_path)


def moving_average(data, window=3):
    if len(data) < 2:
        return data
    smoothed = []
    for i in range(len(data)):
        start = max(0, i - window + 1)
        avg = sum(data[start:i+1]) / (i - start + 1)
        smoothed.append(avg)
    return smoothed


def format_duration(seconds):
    mins, secs = divmod(int(seconds), 60)
    return f"{mins:02}:{secs:02} (mm:ss)"
