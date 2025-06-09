// l2_flooder.c

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>           // For usleep, getpid, close
#include <sys/socket.h>
#include <netinet/ether.h>    // For ETH_P_ALL, struct ether_header
#include <netpacket/packet.h> // For struct sockaddr_ll
#include <net/if.h>           // For struct ifreq, IFNAMSIZ
#include <arpa/inet.h>        // For htons, inet_pton
#include <sys/ioctl.h>        // For SIOCGIFINDEX, SIOCGIFHWADDR
#include <signal.h>           // For signal handling
#include <math.h>             // For atof
#include <sys/time.h>         // For gettimeofday() for busy-wait
#include <netinet/ip.h>       // For struct ip
#include <netinet/ip6.h>      // For struct ip6_hdr

volatile sig_atomic_t keep_running = 1;

// --- Helper Struct for VLAN Header ---
#pragma pack(push, 1)
struct vlan_ethhdr {
    u_char  ether_dhost[ETH_ALEN];
    u_char  ether_shost[ETH_ALEN];
    u_short ether_vlan_tpid; // 0x8100
    u_short ether_vlan_tci;
    u_short ether_type;
};
#pragma pack(pop)

// Global quiet mode flag
int quiet_mode = 0;

void sig_handler(int signo) {
    if (signo == SIGTERM || signo == SIGINT) {
        if (!quiet_mode) {
            fprintf(stdout, "[+] l2_flooder (PID %d): Signal %d received, initiating shutdown...\n", getpid(), signo);
            fflush(stdout);
        }
        keep_running = 0;
    }
}

void busy_wait_microseconds(long microseconds) {
    if (microseconds <= 0) return;
    struct timeval start_time, current_time, target_end_time;
    gettimeofday(&start_time, NULL);
    target_end_time.tv_sec = start_time.tv_sec;
    target_end_time.tv_usec = start_time.tv_usec + microseconds;
    if (target_end_time.tv_usec >= 1000000) {
        target_end_time.tv_sec += target_end_time.tv_usec / 1000000;
        target_end_time.tv_usec %= 1000000;
    }
    do {
        gettimeofday(&current_time, NULL);
    } while (current_time.tv_sec < target_end_time.tv_sec ||
             (current_time.tv_sec == target_end_time.tv_sec && current_time.tv_usec < target_end_time.tv_usec));
}

unsigned short csum(unsigned short *ptr, int nbytes) {
    long sum = 0;
    unsigned short oddbyte;
    short answer;
    while (nbytes > 1) {
        sum += *ptr++;
        nbytes -= 2;
    }
    if (nbytes == 1) {
        oddbyte = 0;
        *((unsigned char *)&oddbyte) = *(unsigned char *)ptr;
        sum += oddbyte;
    }
    sum = (sum >> 16) + (sum & 0xffff);
    sum += (sum >> 16);
    answer = (short)~sum;
    return answer;
}

int main(int argc, char *argv[]) {
    struct sigaction sa_sig;
    sa_sig.sa_handler = sig_handler;
    sigemptyset(&sa_sig.sa_mask);
    sa_sig.sa_flags = 0;
    sigaction(SIGTERM, &sa_sig, NULL);
    sigaction(SIGINT, &sa_sig, NULL);

    if (argc < 6) {
        fprintf(stderr, "Usage: %s <iface> <size> <ethertype> <mac> <rate> [vlan|auto] [quiet]\n", argv[0]);
        return 1;
    }

    if (argc > 1 && strcmp(argv[argc - 1], "quiet") == 0) {
        quiet_mode = 1;
    }

    char *iface = argv[1];
    int packet_size = atoi(argv[2]);
    unsigned short ethertype_input = (unsigned short)strtol(argv[3], NULL, 16);
    const char *dst_mac_str = argv[4];
    double target_mbps = atof(argv[5]);
    const char *vlan_id_str = (argc > 6 && !quiet_mode) ? argv[6] : (argc > 7 && quiet_mode) ? argv[6] : "0";

    if (packet_size < 60) packet_size = 60;
    if (packet_size > 1518) packet_size = 1518;

    int vlan_id = atoi(vlan_id_str);
    int cycle_vlan = (strcmp(vlan_id_str, "auto") == 0);
    int use_vlan = (vlan_id > 0) || cycle_vlan;
    if (cycle_vlan) vlan_id = 1;

    if (use_vlan && packet_size < 64) packet_size = 64;

    int sockfd = socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL));
    if (sockfd < 0) { perror("socket creation failed"); return 1; }

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

    uint8_t *payload_ptr;
    int payload_len;
    int headers_built = 0;

    if (use_vlan) {
        struct vlan_ethhdr *hdr = (struct vlan_ethhdr *)frame;
        sscanf(dst_mac_str, "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx", &hdr->ether_dhost[0], &hdr->ether_dhost[1], &hdr->ether_dhost[2], &hdr->ether_dhost[3], &hdr->ether_dhost[4], &hdr->ether_dhost[5]);
        memcpy(hdr->ether_shost, if_mac.ifr_hwaddr.sa_data, ETH_ALEN);
        hdr->ether_vlan_tpid = htons(0x8100);
        hdr->ether_vlan_tci = htons((unsigned short)vlan_id);
        hdr->ether_type = htons(ethertype_input);
        payload_ptr = frame + sizeof(struct vlan_ethhdr);
        payload_len = packet_size - sizeof(struct vlan_ethhdr);
    } else {
        struct ether_header *hdr = (struct ether_header *)frame;
        sscanf(dst_mac_str, "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx", &hdr->ether_dhost[0], &hdr->ether_dhost[1], &hdr->ether_dhost[2], &hdr->ether_dhost[3], &hdr->ether_dhost[4], &hdr->ether_dhost[5]);
        memcpy(hdr->ether_shost, if_mac.ifr_hwaddr.sa_data, ETH_ALEN);
        hdr->ether_type = htons(ethertype_input);
        payload_ptr = frame + sizeof(struct ether_header);
        payload_len = packet_size - sizeof(struct ether_header);
    }

    if (ethertype_input == 0x0800 && payload_len >= sizeof(struct ip)) {
        struct ip *iph = (struct ip *)payload_ptr;
        iph->ip_v = 4; iph->ip_hl = 5; iph->ip_tos = 0;
        iph->ip_len = htons(payload_len);
        iph->ip_id = htons(getpid());
        iph->ip_off = 0; iph->ip_ttl = 64;
        iph->ip_p = 253; // Testing protocol
        iph->ip_sum = 0;
        inet_pton(AF_INET, "192.168.100.1", &(iph->ip_src));
        inet_pton(AF_INET, "192.168.100.2", &(iph->ip_dst));
        iph->ip_sum = csum((unsigned short *)iph, iph->ip_hl * 4);
        headers_built = 1;
    } else if (ethertype_input == 0x86DD && payload_len >= sizeof(struct ip6_hdr)) {
        struct ip6_hdr *ip6h = (struct ip6_hdr *)payload_ptr;
        ip6h->ip6_flow = 0; ip6h->ip6_vfc = 6 << 4;
        ip6h->ip6_plen = htons(payload_len - sizeof(struct ip6_hdr));
        ip6h->ip6_nxt = 59; // No Next Header
        ip6h->ip6_hlim = 64;
        inet_pton(AF_INET6, "2001:db8:c0de::1", &(ip6h->ip6_src));
        inet_pton(AF_INET6, "2001:db8:c0de::2", &(ip6h->ip6_dst));
        headers_built = 1;
    }

    int data_offset = 0;
    if(headers_built) {
         if(ethertype_input == 0x0800) data_offset = sizeof(struct ip);
         else if(ethertype_input == 0x86DD) data_offset = sizeof(struct ip6_hdr);
    }
    for (int i = data_offset; i < payload_len; i++) {
        payload_ptr[i] = (uint8_t)(i % 256);
    }

    if (!quiet_mode) {
        long packet_size_bits_l2 = (long)packet_size * 8;
        double pps_target = (target_mbps * 1000000.0) / (double)packet_size_bits_l2;
        long delay_microseconds = (pps_target > 0) ? (long)(1000000.0 / pps_target) : 1000000;

        fprintf(stdout, "[+] l2_flooder (PID %d) v2.2 - Quiet Mode Support - COMPILED %s %s\n", getpid(), __DATE__, __TIME__);
        fprintf(stdout, "[+] l2_flooder: Interface: %s, Packet Size: %d bytes, Dst MAC: %s\n", iface, packet_size, dst_mac_str);
        if(use_vlan) fprintf(stdout, "[+] l2_flooder: VLAN Tagging: %s, Inner EtherType: 0x%04X\n", cycle_vlan ? "Auto (Cycling)" : vlan_id_str, ethertype_input);
        else fprintf(stdout, "[+] l2_flooder: EtherType: 0x%04X\n", ethertype_input);
        fprintf(stdout, "[+] l2_flooder: Target Rate: %.2f Mbps, PPS: %.2f, Delay: %ld us\n", target_mbps, pps_target, delay_microseconds);

        const long BUSY_WAIT_THRESHOLD_US = 250;
        if (delay_microseconds > 0 && delay_microseconds < BUSY_WAIT_THRESHOLD_US) {
            fprintf(stdout, "[+] l2_flooder: Using BUSY-WAIT for delay.\n");
        } else if (delay_microseconds >= BUSY_WAIT_THRESHOLD_US) {
            fprintf(stdout, "[+] l2_flooder: Using USLEEP for delay.\n");
        }
        fflush(stdout);
    }

    long packet_size_bits_l2 = (long)packet_size * 8;
    double pps_target = (target_mbps * 1000000.0) / (double)packet_size_bits_l2;
    long delay_microseconds = (pps_target > 0) ? (long)(1000000.0 / pps_target) : 1000000;
    const long BUSY_WAIT_THRESHOLD_US = 250;

    unsigned long packets_sent = 0;
    while (keep_running) {
        if (cycle_vlan) {
            struct vlan_ethhdr *hdr = (struct vlan_ethhdr *)frame;
            vlan_id++;
            if (vlan_id > 4094) vlan_id = 1;
            hdr->ether_vlan_tci = htons((unsigned short)vlan_id);
        }

        if (ethertype_input == 0x0800 && headers_built) {
            struct ip *iph = (struct ip *)payload_ptr;
            iph->ip_id = htons(ntohs(iph->ip_id) + 1);
            iph->ip_sum = 0;
            iph->ip_sum = csum((unsigned short *)iph, iph->ip_hl * 4);
        }

        if (sendto(sockfd, frame, packet_size, 0, (struct sockaddr*)&sa_dest, sizeof(sa_dest)) < 0) {
            if (keep_running) perror("sendto failed");
            break;
        }
        packets_sent++;

        if (delay_microseconds > 0) {
            if (delay_microseconds < BUSY_WAIT_THRESHOLD_US) busy_wait_microseconds(delay_microseconds);
            else usleep(delay_microseconds);
        }
    }

    if (!quiet_mode) {
        fprintf(stdout, "[+] l2_flooder (PID %d): Exiting. Total packets sent: %lu.\n", getpid(), packets_sent);
    }
    // Always print this machine-readable line for the Python script to parse
    fprintf(stdout, "FINAL_STATS:PacketsSent=%lu\n", packets_sent);
    fflush(stdout);

    free(frame);
    close(sockfd);
    return 0;
}