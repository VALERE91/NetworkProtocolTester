use serde::{Deserialize, Serialize};
use tracing::info;
use crate::protocols::{ProtocolMessage, ProtocolType};

#[derive(Default, Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Packet {
    id: u64,
    timestamp: u64,
    latency: u64,
    reliable: bool,
    padding_size: u16,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct TestResult {
    protocol: ProtocolType,
    average_latency: u64,
    min_latency: u64,
    max_latency: u64,
    reliable_packets_sent: u64,
    unreliable_packets_sent: u64,
    packets_received: u64,
    packets_lost: u64,
    packets: Vec<Packet>
}

impl TestResult {
    pub fn new(protocol: ProtocolType) -> Self {
        Self {
            protocol,
            average_latency: 0,
            min_latency: 0,
            max_latency: 0,
            reliable_packets_sent: 0,
            unreliable_packets_sent: 0,
            packets_received: 0,
            packets_lost: 0,
            packets: Vec::new()
        }
    }
    
    pub fn packet_sent(&mut self, reliable: bool) -> u64 {
        if reliable {
            self.reliable_packets_sent += 1;
            self.reliable_packets_sent
        }
        else {
            self.unreliable_packets_sent += 1;
            self.unreliable_packets_sent
        }
    }
    
    pub fn add_packet(&mut self, packet: &ProtocolMessage) {
        self.packets.push(Packet {
            id: packet.id.into(),
            timestamp: packet.timestamp,
            reliable: packet.reliable,
            padding_size: packet.padding_size,
            latency: packet.latency
        });
    }
    
    pub fn finalize(&mut self) {
        if self.packets.is_empty() {
            info!("No packets received.");
        } else {
            let latencies: Vec<u64> = self.packets.iter().map(|p| p.latency).collect();
            let avg = latencies.iter().sum::<u64>() / latencies.len() as u64;
            info!("Received {} packets", self.packets.len());
            info!("Average latency: {}us", avg);
            info!("Min latency: {}us", latencies.iter().min().unwrap());
            info!("Max latency: {}us", latencies.iter().max().unwrap());
            info!("Reliable packets sent: {}", self.reliable_packets_sent);
            info!("Unreliable packets sent: {}", self.unreliable_packets_sent);
            self.packets_received = self.packets.len() as u64;
            info!("Packets received: {}", self.packets_received);
            self.packets_lost = self.packets_received - self.reliable_packets_sent - self.unreliable_packets_sent;
            info!("Packets lost: {}", self.packets_lost);
        }
    }
}