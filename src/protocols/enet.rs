use std::net::{SocketAddr, UdpSocket};
use std::str::FromStr;
use std::time::Duration;
use async_trait::async_trait;
use rusty_enet as enet;
use rusty_enet::PeerID;
use tokio::sync::mpsc;
use tokio::sync::mpsc::{Receiver, Sender};
use tokio::time::sleep;
use tracing::{info, trace};
use crate::protocols::{Protocol, ProtocolConnector, ProtocolListener, ProtocolMessage};

pub struct EnetProtocol {
    sending_queue_tx: mpsc::Sender<ProtocolMessage>,
    receiving_queue_rx: mpsc::Receiver<ProtocolMessage>
}

#[async_trait]
impl ProtocolListener for EnetProtocol {
    async fn listen(ip: &str, port: u16) -> anyhow::Result<()> {
        let sock = UdpSocket::bind(format!("{ip}:{port}"))?;
        let mut host = enet::Host::new(
            sock,
            enet::HostSettings {
                peer_limit: 32,
                channel_limit: 2,
                compressor: Some(Box::new(enet::RangeCoder::new())),
                checksum: Some(Box::new(enet::crc32)),
                ..Default::default()
            },
        )?;
        loop {
            while let Some(event) = host.service()? {
                match event {
                    enet::Event::Connect { peer, .. } => {
                        info!("Peer {} connected", peer.id().0);
                    }
                    enet::Event::Disconnect { peer, .. } => {
                        info!("Peer {} disconnected", peer.id().0);
                    }
                    enet::Event::Receive {
                        peer,
                        channel_id,
                        packet,
                    } => {
                        _ = peer.send(channel_id, &packet);
                    }
                }
            }
        }
    }
}

#[async_trait]
impl ProtocolConnector for EnetProtocol {
    async fn connect(ip: &str, port: u16, local_ip: &str, local_port: u16)
                     -> anyhow::Result<Box<dyn Protocol>> {
        let sock = UdpSocket::bind(format!("{local_ip}:{local_port}"))?;
        let mut host = enet::Host::new(
            sock,
            enet::HostSettings {
                peer_limit: 2,
                channel_limit: 2,
                compressor: Some(Box::new(enet::RangeCoder::new())),
                checksum: Some(Box::new(enet::crc32)),
                ..Default::default()
            },
        )?;
        let host_addr = format!("{ip}:{port}");
        let peer = host.connect(SocketAddr::from_str(host_addr.as_str())?, 2, 0)?;
        peer.set_ping_interval(100);
        let peer_id = peer.id();

        let (sending_tx, sending_rx) = mpsc::channel::<ProtocolMessage>(1024);
        let (receiving_tx, receiving_rx) = mpsc::channel::<ProtocolMessage>(1024);
        tokio::spawn(async move {
            match enet_client_loop(peer_id, host, sending_rx, receiving_tx).await{
                Ok(_) => {
                    info!("Client loop finished");
                }
                Err(e) => {
                    info!("Client loop failed: {}", e);
                }
            }
        });

        Ok(Box::new(Self{
            sending_queue_tx: sending_tx,
            receiving_queue_rx: receiving_rx,
        }))
    }
}

async fn enet_client_loop(peer_id: PeerID, mut host: enet::Host<UdpSocket>, mut sending_rx: Receiver<ProtocolMessage>, receiving_tx: Sender<ProtocolMessage>) -> anyhow::Result<()>{
    let mut peer_connected = false;
    loop {
        while let Some(event) = host.service()? {
            match event {
                enet::Event::Connect { peer, .. } => {
                    info!("Peer {} Connected", peer.id().0);
                    peer_connected = true;
                }
                enet::Event::Disconnect { .. } => {
                    info!("Peer Disconnected");
                }
                enet::Event::Receive { packet, .. } => {
                    trace!("Received message: {:?}", packet.data().len());
                    let message = ProtocolMessage::from_binary(packet.data())?;
                    receiving_tx.send(message).await?;
                }
            }
        }

        if !peer_connected{
            continue;
        }

        tokio::select! {
            message = sending_rx.recv() => {
                if let Some(message) = message {
                    if message.reliable {
                        let packet = enet::Packet::reliable(message.to_binary()?);
                        host.peer_mut(peer_id).send(0, &packet)?;
                    }

                    let packet = enet::Packet::unreliable(message.to_binary()?);
                    host.peer_mut(peer_id).send(0, &packet)?;
                } else {
                    println!("Sending queue closed");
                    return Ok(());
                }
            },
            _ = sleep(Duration::from_micros(10)) => {

            }
        }
    }
}

#[async_trait]
impl Protocol for EnetProtocol {
    async fn disconnect(&mut self) {

    }

    async fn send(&mut self, message: &ProtocolMessage) -> anyhow::Result<()> {
        Ok(self.sending_queue_tx.send(message.clone()).await?)
    }

    async fn receive(&mut self) -> anyhow::Result<ProtocolMessage> {
        self.receiving_queue_rx.recv().await.ok_or_else(|| anyhow::anyhow!("Failed to receive message"))
    }
}