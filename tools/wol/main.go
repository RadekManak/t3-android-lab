// wol sends a Wake-on-LAN magic packet from the phone, whose toybox nc cannot broadcast.
package main

import (
	"bytes"
	"fmt"
	"net"
	"os"
)

func main() {
	if len(os.Args) < 2 || len(os.Args) > 3 {
		fmt.Fprintln(os.Stderr, "usage: wol <mac> [broadcast-address]")
		os.Exit(2)
	}
	mac, err := net.ParseMAC(os.Args[1])
	if err != nil || len(mac) != 6 {
		fmt.Fprintln(os.Stderr, "wol: expected a 6-byte MAC such as aa:bb:cc:dd:ee:ff")
		os.Exit(2)
	}
	packet := append(bytes.Repeat([]byte{0xff}, 6), bytes.Repeat(mac, 16)...)
	targets := []string{"255.255.255.255"}
	if len(os.Args) == 3 {
		targets = append(targets, os.Args[2])
	}
	sent := 0
	for _, target := range targets {
		// Go enables SO_BROADCAST on IPv4 UDP sockets.
		conn, err := net.Dial("udp4", net.JoinHostPort(target, "9"))
		if err != nil {
			fmt.Fprintln(os.Stderr, "wol:", target, err)
			continue
		}
		if _, err = conn.Write(packet); err != nil {
			fmt.Fprintln(os.Stderr, "wol:", target, err)
		} else {
			sent++
		}
		conn.Close()
	}
	if sent == 0 {
		os.Exit(1)
	}
	fmt.Printf("sent magic packet for %s to %d address(es)\n", mac, sent)
}
