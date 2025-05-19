#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/ether.h>
#include <netpacket/packet.h>
#include <net/if.h>
#include <arpa/inet.h>
#include <sys/ioctl.h>

#define DEFAULT_PACKET_SIZE 1400

int main(int argc, char *argv[]) {
    if (argc < 5) {
        fprintf(stderr, "Usage: %s <interface> <packet_size> <ethertype_hex> <dst_mac>\n", argv[0]);
        return 1;
    }

    char *iface = argv[1];
    int packet_size = atoi(argv[2]);
    unsigned short ethertype_input = (unsigned short)strtol(argv[3], NULL, 16);
    const char *dst_mac_str = argv[4];

    if (packet_size < 64) packet_size = 64;
    if (packet_size > 1500) packet_size = 1500;

    int sockfd = socket(AF_PACKET, SOCK_RAW, htons(ethertype_input));
    if (sockfd < 0) {
        perror("socket");
        return 1;
    }

    struct ifreq if_idx = {0}, if_mac = {0};
    strncpy(if_idx.ifr_name, iface, IFNAMSIZ - 1);
    if (ioctl(sockfd, SIOCGIFINDEX, &if_idx) < 0) {
        perror("SIOCGIFINDEX");
        return 1;
    }

    strncpy(if_mac.ifr_name, iface, IFNAMSIZ - 1);
    if (ioctl(sockfd, SIOCGIFHWADDR, &if_mac) < 0) {
        perror("SIOCGIFHWADDR");
        return 1;
    }

    struct sockaddr_ll sa = {0};
    sa.sll_ifindex = if_idx.ifr_ifindex;
    sa.sll_halen = ETH_ALEN;

    uint8_t *frame = malloc(packet_size);
    memset(frame, 0, packet_size);

    // MAC addresses
    sscanf(dst_mac_str, "%hhx:%hhx:%hhx:%hhx:%hhx:%hhx",
           &frame[0], &frame[1], &frame[2],
           &frame[3], &frame[4], &frame[5]);
    memcpy(&frame[6], if_mac.ifr_hwaddr.sa_data, 6);

    int offset = 12;

    if (ethertype_input == 0x8100) {
        // VLAN TPID
        frame[offset++] = 0x81;
        frame[offset++] = 0x00;
        // TCI (VLAN ID 1)
        frame[offset++] = 0x00;
        frame[offset++] = 0x01;
        // Inner EtherType: IPv4
        frame[offset++] = 0x08;
        frame[offset++] = 0x00;

        // Valid dummy IPv4 header starts here
        int ip_offset = offset;
        frame[ip_offset + 0] = 0x45; // Version=4, IHL=5
        frame[ip_offset + 1] = 0x00; // TOS
        int total_len = packet_size - ip_offset;
        frame[ip_offset + 2] = (total_len >> 8) & 0xFF;
        frame[ip_offset + 3] = total_len & 0xFF;
        frame[ip_offset + 4] = 0x00; frame[ip_offset + 5] = 0x01; // ID
        frame[ip_offset + 6] = 0x00; frame[ip_offset + 7] = 0x00; // Flags/Frag
        frame[ip_offset + 8] = 64;   // TTL
        frame[ip_offset + 9] = 17;   // UDP
        frame[ip_offset + 10] = 0x00; frame[ip_offset + 11] = 0x00; // Checksum (skip)
        frame[ip_offset + 12] = 10; frame[ip_offset + 13] = 0; frame[ip_offset + 14] = 0; frame[ip_offset + 15] = 1;
        frame[ip_offset + 16] = 192; frame[ip_offset + 17] = 168; frame[ip_offset + 18] = 0; frame[ip_offset + 19] = 1;

    } else if (ethertype_input == 0x8847) {
        // MPLS EtherType
        frame[offset++] = 0x88;
        frame[offset++] = 0x47;

        // Add 4-byte MPLS label stack
        frame[offset++] = 0x00; // Label high
        frame[offset++] = 0x10;
        frame[offset++] = 0x04;
        frame[offset++] = 0x40; // TTL

    } else {
        // Normal EtherType (ARP, IPv6, 0xFFFF, etc.)
        frame[offset++] = (ethertype_input >> 8) & 0xFF;
        frame[offset++] = ethertype_input & 0xFF;
    }

    // Fill rest of the frame with dummy payload
    for (int i = offset; i < packet_size; i++) {
        frame[i] = (uint8_t)(i & 0xFF);
    }

    printf("[+] Sending %d-byte frame on %s with EtherType 0x%04X → %s\n",
           packet_size, iface, ethertype_input, dst_mac_str);

    while (1) {
        if (sendto(sockfd, frame, packet_size, 0, (struct sockaddr*)&sa, sizeof(sa)) < 0) {
            perror("sendto");
            break;
        }
    }

    free(frame);
    close(sockfd);
    return 0;
}
