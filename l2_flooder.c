// l2_flooder.c

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h> // For usleep, getpid, close
#include <sys/socket.h>
#include <netinet/ether.h> // For ETH_P_ALL
#include <netpacket/packet.h> // For struct sockaddr_ll
#include <net/if.h> // For struct ifreq, IFNAMSIZ
#include <arpa/inet.h> // For htons
#include <sys/ioctl.h> // For SIOCGIFINDEX, SIOCGIFHWADDR
#include <signal.h> // For signal handling (sig_atomic_t, sigaction)
#include <math.h>   // For atof
#include <sys/time.h> // For gettimeofday() for busy-wait

volatile sig_atomic_t keep_running = 1;

void sig_handler(int signo) {
    if (signo == SIGTERM || signo == SIGINT) {
        fprintf(stdout, "[+] l2_flooder (PID %d): Signal %d received, initiating shutdown...\n", getpid(), signo);
        fflush(stdout);
        keep_running = 0;
    }
}

// Busy-wait function for precise short delays
void busy_wait_microseconds(long microseconds) {
    if (microseconds <= 0) return;

    struct timeval start_time, current_time, target_end_time;
    gettimeofday(&start_time, NULL);

    target_end_time.tv_sec = start_time.tv_sec;
    target_end_time.tv_usec = start_time.tv_usec + microseconds;

    // Normalize target_end_time (handle microsecond overflow)
    if (target_end_time.tv_usec >= 1000000) {
        target_end_time.tv_sec += target_end_time.tv_usec / 1000000;
        target_end_time.tv_usec %= 1000000;
    }

    do {
        gettimeofday(&current_time, NULL);
    } while (current_time.tv_sec < target_end_time.tv_sec ||
             (current_time.tv_sec == target_end_time.tv_sec && current_time.tv_usec < target_end_time.tv_usec));
}


int main(int argc, char *argv[]) {
    struct sigaction sa_sig;
    sa_sig.sa_handler = sig_handler;
    sigemptyset(&sa_sig.sa_mask);
    sa_sig.sa_flags = 0;
    if (sigaction(SIGTERM, &sa_sig, NULL) == -1) { perror("Error: cannot handle SIGTERM"); return 1; }
    if (sigaction(SIGINT, &sa_sig, NULL) == -1) { perror("Error: cannot handle SIGINT"); return 1; }

    if (argc < 6) {
        fprintf(stderr, "Usage: %s <interface> <packet_size_bytes> <ethertype_hex_str> <dst_mac_hex_str> <target_mbps>\n", argv[0]);
        return 1;
    }

    char *iface = argv[1];
    int packet_size = atoi(argv[2]);
    unsigned short ethertype_input = (unsigned short)strtol(argv[3], NULL, 16);
    const char *dst_mac_str = argv[4];
    double target_mbps = atof(argv[5]);

    if (packet_size < 60) packet_size = 60;
    if (packet_size > 1500) packet_size = 1500;

    if (target_mbps <= 0) {
        fprintf(stderr, "[ERROR] Target Mbps must be positive. Received: %.2f\n", target_mbps);
        target_mbps = 1.0;
        fprintf(stderr, "[Warning] Defaulting target rate to %.2f Mbps.\n", target_mbps);
    }

    int sockfd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (sockfd < 0) {
        perror("socket creation failed");
        return 1;
    }

    struct ifreq if_idx = {0}, if_mac = {0};
    strncpy(if_idx.ifr_name, iface, IFNAMSIZ - 1);
    if (ioctl(sockfd, SIOCGIFINDEX, &if_idx) < 0) { perror("SIOCGIFINDEX failed"); close(sockfd); return 1; }
    strncpy(if_mac.ifr_name, iface, IFNAMSIZ - 1);
    if (ioctl(sockfd, SIOCGIFHWADDR, &if_mac) < 0) { perror("SIOCGIFHWADDR failed"); close(sockfd); return 1; }

    struct sockaddr_ll sa_dest = {0};
    sa_dest.sll_ifindex = if_idx.ifr_ifindex;
    sa_dest.sll_halen = ETH_ALEN;

    uint8_t *frame = (uint8_t *)malloc(packet_size);
    if (!frame) { perror("malloc for frame failed"); close(sockfd); return 1; }
    memset(frame, 0, packet_size);

    if (sscanf(dst_mac_str, "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx",
           &frame[0], &frame[1], &frame[2], &frame[3], &frame[4], &frame[5]) != 6) {
        fprintf(stderr, "Error parsing destination MAC address: %s\n", dst_mac_str);
        free(frame); close(sockfd); return 1;
    }
    memcpy(&frame[6], if_mac.ifr_hwaddr.sa_data, ETH_ALEN);
    frame[12] = (ethertype_input >> 8) & 0xFF;
    frame[13] = ethertype_input & 0xFF;
    for (int i = 14; i < packet_size; i++) {
        frame[i] = (uint8_t)(i % 256);
    }

    long packet_size_bits_l2 = (long)packet_size * 8;
    double packets_per_second_target = 0;
    if (packet_size_bits_l2 > 0) {
        packets_per_second_target = (target_mbps * 1000000.0) / (double)packet_size_bits_l2;
    }

    long delay_microseconds = 0;
    if (packets_per_second_target > 0.000001) {
        delay_microseconds = (long)(1000000.0 / packets_per_second_target);
    } else {
        delay_microseconds = 1000000;
        if (target_mbps > 0) {
             fprintf(stdout, "[+] l2_flooder: Calculated pps is near zero (target_mbps=%.2f, packet_size_bits_l2=%ld), defaulting delay to 1s.\n", target_mbps, packet_size_bits_l2);
        }
    }

    fprintf(stdout, "[+] l2_flooder (PID %d) version: SIG HANDLER ENABLED, BUSY-WAIT ENABLED - COMPILED %s %s\n", getpid(), __DATE__, __TIME__);
    fprintf(stdout, "[+] l2_flooder: Interface: %s, Packet Size (L2 Frame): %d bytes (%ld bits L2)\n", iface, packet_size, packet_size_bits_l2);
    fprintf(stdout, "[+] l2_flooder: Dst MAC: %s, EtherType: 0x%04X\n", dst_mac_str, ethertype_input);
    fprintf(stdout, "[+] l2_flooder: Target Rate: %.2f Mbps, Calculated PPS: %.2f, Calculated Inter-packet Delay: %ld us\n",
           target_mbps, packets_per_second_target, delay_microseconds);


    // --- MODIFICATION FOR THROUGHPUT: HYBRID DELAY ---
    // Threshold below which busy-waiting is used instead of usleep.
    // Increased to cover cases where usleep is imprecise (e.g., for delays around 150-200us).
    const long BUSY_WAIT_THRESHOLD_US = 250; // microseconds (Increased from 150)

    if (delay_microseconds < 0) { // Safeguard
        fprintf(stderr, "[ERROR] l2_flooder: Negative delay calculated (%ld us). Setting to 0 (max speed).\n", delay_microseconds);
        delay_microseconds = 0;
    }

    if (delay_microseconds > 0 && delay_microseconds < BUSY_WAIT_THRESHOLD_US) {
        fprintf(stdout, "[+] l2_flooder: Calculated delay %ld us is < %ld us threshold. Using BUSY-WAIT.\n",
                delay_microseconds, BUSY_WAIT_THRESHOLD_US);
    } else if (delay_microseconds >= BUSY_WAIT_THRESHOLD_US) {
        fprintf(stdout, "[+] l2_flooder: Calculated delay %ld us is >= %ld us threshold. Using USLEEP.\n",
                delay_microseconds, BUSY_WAIT_THRESHOLD_US);
    } else { // delay_microseconds is 0
        fprintf(stdout, "[+] l2_flooder: Using 0 delay (max speed).\n");
    }
    fflush(stdout);
    // --- END MODIFICATION ---

    unsigned long packets_sent = 0;
    while (keep_running) {
        if (sendto(sockfd, frame, packet_size, 0, (struct sockaddr*)&sa_dest, sizeof(sa_dest)) < 0) {
            if (keep_running) {
                perror("sendto failed");
            }
            break;
        }
        packets_sent++;

        // Apply delay using hybrid approach
        if (delay_microseconds > 0) {
            if (delay_microseconds < BUSY_WAIT_THRESHOLD_US) {
                busy_wait_microseconds(delay_microseconds);
            } else {
                usleep(delay_microseconds);
            }
        }
        // If delay_microseconds is 0, no delay is applied (max speed)
    }

    fprintf(stdout, "[+] l2_flooder (PID %d): Exiting. Total packets sent: %lu.\n", getpid(), packets_sent);
    fflush(stdout);
    free(frame);
    close(sockfd);
    return 0;
}
