# remote_rx_agent.py
import psutil
import time
import sys
import os  # For PID


def stream_remote_rx_stats(iface_name, interval=1.0):
    if not hasattr(psutil, 'net_io_counters'):
        print("ERROR:psutil_not_found_or_incomplete_on_remote", flush=True)
        sys.exit(1)

    try:
        if iface_name not in psutil.net_if_stats():  # Check interface existence using a lightweight call
            print(f"ERROR:Interface_not_found_on_remote:{iface_name}", flush=True)
            available_ifaces = list(psutil.net_io_counters(pernic=True).keys())  # Get keys if available
            print(
                f"INFO:Available_interfaces_on_remote:{','.join(available_ifaces) if available_ifaces else 'None_found'}",
                flush=True)
            sys.exit(1)

        last_stats = psutil.net_io_counters(pernic=True).get(iface_name)
        if not last_stats:
            print(f"ERROR:Could_not_get_initial_stats_for:{iface_name}", flush=True)
            sys.exit(1)

        last_time = time.monotonic()
        print(f"INFO:Monitoring_started_on_remote_interface:{iface_name}_interval:{interval:.1f}s_pid:{os.getpid()}",
              flush=True)

        loop_iteration = 0
        while True:
            loop_iteration += 1
            current_monotonic_time_for_debug = time.monotonic()
            print(
                f"DEBUG_REMOTE_AGENT_LOOP:{loop_iteration}|Time:{current_monotonic_time_for_debug:.2f}|Interface:{iface_name}|About to sleep for {interval:.1f}s",
                flush=True)

            time.sleep(interval)

            current_stats = psutil.net_io_counters(pernic=True).get(iface_name)
            current_time = time.monotonic()

            if not current_stats:
                print(f"ERROR:Interface_no_longer_found_on_remote:{iface_name}", flush=True)
                break

            time_diff = current_time - last_time
            if time_diff <= 0:
                print(
                    f"DEBUG_REMOTE_AGENT_WARN:Time_diff_non_positive ({time_diff:.4f}), using nominal interval {interval:.1f}s.",
                    flush=True)
                time_diff = interval  # Use nominal interval if time_diff is bad
                if time_diff <= 0: time_diff = 1.0  # Absolute fallback

            bytes_recv_diff = current_stats.bytes_recv - last_stats.bytes_recv
            if bytes_recv_diff < 0:
                print(
                    f"DEBUG_REMOTE_AGENT_WARN:Byte_counter_wrap_or_reset (diff: {bytes_recv_diff}), setting diff to 0 for this interval.",
                    flush=True)
                bytes_recv_diff = 0

            rx_rate_mbps = (bytes_recv_diff * 8) / time_diff / 1_000_000

            print(
                f"DEBUG_REMOTE_AGENT_CALC:bytes_diff={bytes_recv_diff},time_diff={time_diff:.4f},calc_mbps={rx_rate_mbps:.2f}",
                flush=True)
            print(f"DATA:{rx_rate_mbps:.2f}", flush=True)

            last_stats = current_stats
            last_time = current_time

    except KeyboardInterrupt:
        print("INFO:Remote_monitoring_stopped_by_interrupt_or_channel_close", flush=True)
    except Exception as e:
        print(f"ERROR:Remote_script_exception:{type(e).__name__}:{str(e).splitlines()[0]}",
              flush=True)  # Print concise error
        # For more detail, could print traceback.format_exc() here too if needed for remote debug
    finally:
        print("INFO:Remote_monitoring_script_exiting", flush=True)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 remote_rx_agent.py <interface_name> [interval_seconds]", flush=True)
        sys.exit(1)

    interface_to_monitor = sys.argv[1]
    monitoring_interval = 1.0
    if len(sys.argv) > 2:
        try:
            monitoring_interval = float(sys.argv[2])
            if monitoring_interval <= 0:
                raise ValueError("Interval must be positive")
        except ValueError as ve:
            print(f"ERROR:Invalid_interval_value:'{sys.argv[2]}'._{ve}", flush=True)
            sys.exit(1)

    stream_remote_rx_stats(interface_to_monitor, monitoring_interval)