use std::fmt::Display;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::OnceLock;
use async_trait::async_trait;
use bincode::{Decode, Encode};
use serde::{Deserialize, Serialize};
use tokio::time::Instant;
use tracing::{error, info};
use crate::protocols::enet::EnetProtocol;
use crate::protocols::tcp::TcpProtocol;
use crate::protocols::udp::UdpProtocol;
use crate::protocols::quic::QuicProtocol;

mod udp;
mod tcp;
mod quic;
mod enet;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, clap::ValueEnum, Serialize, Deserialize)]
pub enum ProtocolType {
    Udp,
    Tcp,
    Quic,
    Enet
}

impl Display for ProtocolType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let str = match self {
            ProtocolType::Udp => "udp",
            ProtocolType::Tcp => "tcp",
            ProtocolType::Quic => "quic",
            ProtocolType::Enet => "enet"
        };
        write!(f, "{}", str)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Encode, Decode, Serialize, Deserialize)]
pub struct ProtocolMessage{
    pub id: u32,
    pub timestamp: u64,
    pub padding_size: u16,
    padding: Vec<u8>,
    pub reliable: bool,
    #[serde(skip)]
    pub latency: u64
}

static PACKET_COUNTER: AtomicU32 = AtomicU32::new(0);
static APP_EPOCH: OnceLock<Instant> = OnceLock::new();

impl ProtocolMessage {
    pub fn new(padding_size: u16, reliable: bool) -> Self {
        Self {
            id : PACKET_COUNTER.fetch_add(1, Ordering::Relaxed),
            timestamp: ProtocolMessage::monotonic_micros(),
            padding_size,
            reliable,
            padding: ProtocolMessage::generate_padding(padding_size),
            latency: 0
        }
    }

    /// Returns a monotonic u64 timestamp in microseconds
    /// since the program first called this function.
    fn monotonic_micros() -> u64 {
        APP_EPOCH
            .get_or_init(Instant::now)
            .elapsed()
            .as_micros() as u64
    }

    /// Generates a Vec<u8> of size `size` filled with random values.
    fn generate_padding(size: u16) -> Vec<u8> {
        let mut padding = Vec::with_capacity(size as usize);
        for _ in 0..size {
            padding.push(rand::random::<u8>());
        }
        padding
    }

    /// Serializes the ProtocolMessage into a binary Vec<u8>.
    pub fn to_binary(&self) -> anyhow::Result<Vec<u8>> {
        Ok(bincode::encode_to_vec(self, bincode::config::standard())?)
    }

    /// Deserializes a ProtocolMessage from a binary slice.
    pub fn from_binary(bytes: &[u8]) -> anyhow::Result<Self> {
        let mut decoded: ProtocolMessage = bincode::decode_from_slice(bytes, bincode::config::standard())?.0;
        decoded.latency = ProtocolMessage::monotonic_micros() - decoded.timestamp;
        Ok(decoded)
    }
}

impl Display for ProtocolMessage {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "id: {}, padding_size: {}, latency: {}us",
               self.id, self.padding_size, self.latency)
    }
}

#[async_trait]
pub trait Protocol {
    /// Disconnects from the server.
    async fn disconnect(&mut self);

    /// Sends data to the server.
    async fn send(&mut self, message: &ProtocolMessage) -> anyhow::Result<()>;

    /// Receives data from the server.
    async fn receive(&mut self) -> anyhow::Result<ProtocolMessage>;
}

#[async_trait]
pub trait ProtocolListener {
    /// Listens for incoming connections on ip:port.
    async fn listen(ip: &str, port: u16) -> anyhow::Result<()>;
}

#[async_trait]
pub trait ProtocolConnector {
    /// Connects to a server located at ip:port.
    async fn connect(ip: &str, port: u16, local_ip: &str, local_port: u16)
        -> anyhow::Result<Box<dyn Protocol>>;
}

pub struct ServerConfig {
    pub ip: Option<String>,
    pub port: u16,
    pub protocol: ProtocolType,
}

pub struct ClientConfig {
    pub host_ip: String,
    pub host_port: u16,
    pub local_ip: Option<String>,
    pub local_port: Option<u16>,
    pub protocol: ProtocolType,
}

pub async fn start_server(config: ServerConfig) {
    match config.protocol {
        ProtocolType::Udp => {
            match UdpProtocol::listen(config.ip.as_deref().unwrap_or("0.0.0.0"),
                                      config.port).await{
                Ok(_) => { info!("Server started on port: {}", config.port); },
                Err(e) => { error!("Failed to start server: {}", e.to_string()); }
            }
        }
        ProtocolType::Tcp => {
            match TcpProtocol::listen(config.ip.as_deref().unwrap_or("0.0.0.0"),
                                      config.port).await{
                Ok(_) => { info!("Server started on port: {}", config.port); },
                Err(e) => { error!("Failed to start server: {}", e.to_string()); }
            }
        }
        ProtocolType::Quic => {
            match QuicProtocol::listen(config.ip.as_deref().unwrap_or("0.0.0.0"),
                                      config.port).await{
                Ok(_) => { info!("Server started on port: {}", config.port); },
                Err(e) => { error!("Failed to start server: {}", e.to_string()); }
            }
        }
        ProtocolType::Enet => {
            match EnetProtocol::listen(config.ip.as_deref().unwrap_or("0.0.0.0"),
                                       config.port).await{
                Ok(_) => { info!("Server started on port: {}", config.port); },
                Err(e) => { error!("Failed to start server: {}", e.to_string()); }
            }
        }
    }
}

pub async fn start_client(config: ClientConfig) -> anyhow::Result<Box<dyn Protocol>> {
    match config.protocol {
        ProtocolType::Udp => {
            UdpProtocol::connect(config.host_ip.as_str(),
                                   config.host_port,
                                    config.local_ip.as_deref().unwrap_or("0.0.0.0"),
                                    config.local_port.unwrap_or(0)).await
        }
        ProtocolType::Tcp => {
            TcpProtocol::connect(config.host_ip.as_str(),
                                   config.host_port,
                                    config.local_ip.as_deref().unwrap_or("0.0.0.0"),
                                    config.local_port.unwrap_or(0)).await
        }
        ProtocolType::Quic => {
            QuicProtocol::connect(config.host_ip.as_str(),
                                   config.host_port,
                                    config.local_ip.as_deref().unwrap_or("0.0.0.0"),
                                    config.local_port.unwrap_or(0)).await
        }
        ProtocolType::Enet => {
            EnetProtocol::connect(config.host_ip.as_str(),
                                  config.host_port,
                                  config.local_ip.as_deref().unwrap_or("0.0.0.0"),
                                  config.local_port.unwrap_or(0)).await
        }
    }
}