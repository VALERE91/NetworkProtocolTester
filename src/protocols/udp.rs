use std::net::SocketAddr;
use async_trait::async_trait;
use tokio::net::UdpSocket;
use tracing::trace;
use crate::protocols::{Protocol, ProtocolConnector, ProtocolListener, ProtocolMessage};

pub struct UdpProtocol {
    socket: UdpSocket,
    remote_addr: SocketAddr
}

#[async_trait]
impl ProtocolListener for UdpProtocol {
    async fn listen(ip: &str, port: u16) -> anyhow::Result<()> {
        let sock = UdpSocket::bind(format!("{ip}:{port}")).await?;
        let mut buf = [0; 65535];
        loop {
            let (len, addr) = sock.recv_from(&mut buf).await?;
            trace!("{:?} bytes received from {:?}", len, addr);

            let len = sock.send_to(&buf[..len], addr).await?;
            trace!("{:?} bytes sent", len);
        }
    }
}

#[async_trait]
impl ProtocolConnector for UdpProtocol {
    async fn connect(ip: &str, port: u16, local_ip: &str, local_port: u16)
        -> anyhow::Result<Box<dyn Protocol>> {
        let sock = UdpSocket::bind(format!("{local_ip}:{local_port}")).await?;
        Ok(Box::new(Self{
            socket: sock,
            remote_addr: SocketAddr::new(ip.parse()?, port)
        }))
    }
}

#[async_trait]
impl Protocol for UdpProtocol {
    async fn disconnect(&mut self) {

    }

    async fn send(&mut self, message: &ProtocolMessage) -> anyhow::Result<()> {
        self.socket.send_to(&message.to_binary()?, self.remote_addr).await?;
        Ok(())
    }

    async fn receive(&mut self) -> anyhow::Result<ProtocolMessage> {
        let mut buf = [0; 65535];
        let (size, addr) = self.socket.recv_from(&mut buf).await?;
        trace!("{:?} bytes received from {:?}", size, addr);
        let message = ProtocolMessage::from_binary(&buf[..size])?;
        Ok(message)
    }
}