// l2_flooder.c

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h> // For usleep, getpid
#include <sys/socket.h>
#include <netinet/ether.h>
#include <netpacket/packet.h>
#include <net/if.h>
#include <arpa/inet.h>
#include <sys/ioctl.h>
#include <signal.h> // For signal handling
#include <math.h>   // For round (or use integer arithmetic)

volatile sig_atomic_t keep_running = 1;

void sig_handler(int signo) {
    if (signo == SIGTERM || signo == SIGINT) {
        // Note: printf is not strictly async-signal-safe, but often used for simple debug.
        // For production, writing to a pipe or setting a flag is safer.
        // This process will print to its own stdout, captured by the Python script.
        fprintf(stdout, "[+] l2_flooder (PID %d): Signal %d received, shutting down...\n", getpid(), signo);
        fflush(stdout);
        keep_running = 0;
    }
}

int main(int argc, char *argv[]) {
    struct sigaction sa_sig;
    sa_sig.sa_handler = sig_handler;
    sigemptyset(&sa_sig.sa_mask);
    sa_sig.sa_flags = 0;
    if (sigaction(SIGTERM, &sa_sig, NULL) == -1) { perror("Error: cannot handle SIGTERM"); }
    if (sigaction(SIGINT, &sa_sig, NULL) == -1) { perror("Error: cannot handle SIGINT"); }

    // Expected: <interface> <packet_size> <ethertype_hex> <dst_mac> <target_mbps>
    if (argc < 6) {
        fprintf(stderr, "Usage: %s <interface> <packet_size_bytes> <ethertype_hex> <dst_mac_hex> <target_mbps>\n", argv[0]);
        return 1;
    }

    char *iface = argv[1];
    int packet_size = atoi(argv[2]); // e.g., 1400 bytes
    unsigned short ethertype_input = (unsigned short)strtol(argv[3], NULL, 16);
    const char *dst_mac_str = argv[4];
    double target_mbps = atof(argv[5]); // e.g., 50.0

    if (packet_size < 60) packet_size = 60; // Min Ethernet frame (excluding FCS which NIC adds)
    if (packet_size > 1500) packet_size = 1500; // Standard MTU for Ethernet payload + L2 header

    if (target_mbps <= 0) {
        fprintf(stderr, "[ERROR] Target Mbps must be positive. Received: %.2f\n", target_mbps);
        // Defaulting to a low rate to prevent accidental flooding if error in input
        target_mbps = 1.0;
        fprintf(stderr, "[Warning] Defaulting target rate to %.2f Mbps.\n", target_mbps);
    }

    int sockfd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL)); // Send raw, capture all for socket type
    if (sockfd < 0) {
        perror("socket");
        return 1;
    }

    struct ifreq if_idx = {0}, if_mac = {0};
    strncpy(if_idx.ifr_name, iface, IFNAMSIZ - 1);
    if (ioctl(sockfd, SIOCGIFINDEX, &if_idx) < 0) { perror("SIOCGIFINDEX"); close(sockfd); return 1; }
    strncpy(if_mac.ifr_name, iface, IFNAMSIZ - 1);
    if (ioctl(sockfd, SIOCGIFHWADDR, &if_mac) < 0) { perror("SIOCGIFHWADDR"); close(sockfd); return 1; }

    struct sockaddr_ll sa_dest = {0};
    sa_dest.sll_ifindex = if_idx.ifr_ifindex;
    sa_dest.sll_halen = ETH_ALEN;
    // Destination MAC is set in the frame directly

    uint8_t *frame = (uint8_t *)malloc(packet_size);
    if (!frame) { perror("malloc"); close(sockfd); return 1; }
    memset(frame, 0, packet_size);

    sscanf(dst_mac_str, "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx",
           &frame[0], &frame[1], &frame[2], &frame[3], &frame[4], &frame[5]);
    memcpy(&frame[6], if_mac.ifr_hwaddr.sa_data, ETH_ALEN); // Source MAC

    // EtherType
    frame[12] = (ethertype_input >> 8) & 0xFF;
    frame[13] = ethertype_input & 0xFF;

    // Fill payload (from byte 14 onwards)
    for (int i = 14; i < packet_size; i++) {
        frame[i] = (uint8_t)(i % 256);
    }

    long packet_size_bits = (long)packet_size * 8;
    double packets_per_second_target = 0;
    if (packet_size_bits > 0) { // Avoid division by zero if packet_size_bits is somehow zero
        packets_per_second_target = (target_mbps * 1000000.0) / (double)packet_size_bits;
    }

    long delay_microseconds = 0;
    if (packets_per_second_target > 0.000001) { // Avoid division by zero if pps is effectively zero
        delay_microseconds = (long)(1000000.0 / packets_per_second_target);
    } else { // Very low rate or error, default to a large delay
        delay_microseconds = 1000000; // ~1 packet per second
        if (target_mbps > 0) { // If target Mbps was positive but pps is ~0 (e.g. huge packets, tiny rate)
             fprintf(stdout, "[+] l2_flooder: Calculated pps is near zero, defaulting delay to 1s.\n");
        }
    }

    // Practical minimum delay: if calculated delay is too small (e.g. < 10-20us),
    // usleep might not be effective or CPU usage will be high.
    // However, for achieving high rates, small delays are necessary.
    // If delay_microseconds is 0, it means send as fast as possible up to the target.
    // This calculation means if target_mbps is high enough that pps > 1M, delay_microseconds will be 0.
    // In such a case, the CPU/NIC is the bottleneck, which is fine if testing max capacity.
    // But for controlled lower rates, delay_microseconds will be > 0.

    fprintf(stdout, "[+] l2_flooder (PID %d) version: SIG HANDLER ENABLED - COMPILED %s %s\n", getpid(), __DATE__, __TIME__);
    fprintf(stdout, "[+] l2_flooder: Interface: %s, Packet Size: %d bytes (%ld bits)\n", iface, packet_size, packet_size_bits);
    fprintf(stdout, "[+] l2_flooder: Dst MAC: %s, EtherType: 0x%04X\n", dst_mac_str, ethertype_input);
    fprintf(stdout, "[+] l2_flooder: Target Rate: %.2f Mbps, Calculated PPS: %.2f, Inter-packet Delay: %ld us\n",
           target_mbps, packets_per_second_target, delay_microseconds);
    fflush(stdout);

    while (keep_running) {
        if (sendto(sockfd, frame, packet_size, 0, (struct sockaddr*)&sa_dest, sizeof(sa_dest)) < 0) {
            if (keep_running) { // Only print error if not intentionally stopping
                perror("sendto");
            }
            break;
        }
        if (delay_microseconds > 0) {
            usleep(delay_microseconds);
        }
        // No additional usleep(100) needed here; the rate-limiting delay also allows signals to be processed.
        // If delay_microseconds is 0 (very high target rate), the loop will be CPU-bound;
        // signal processing relies on kernel scheduling. This is usually fine.
    }

    fprintf(stdout, "[+] l2_flooder (PID %d): Exiting cleanly.\n", getpid());
    fflush(stdout);
    free(frame);
    close(sockfd);
    return 0;
}