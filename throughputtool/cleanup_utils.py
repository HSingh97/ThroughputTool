
import subprocess

def kill_iperf():
    subprocess.run(["pkill", "-f", "iperf3"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def kill_ping():
    subprocess.run(["sudo", "killall", "-q", "ping"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def kill_l2():
    subprocess.run(["pkill", "-f", "scapy"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
