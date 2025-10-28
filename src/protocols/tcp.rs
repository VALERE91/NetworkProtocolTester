use tokio::io::{self, AsyncReadExt, AsyncWriteExt};
use async_trait::async_trait;
use tokio::net::{TcpListener, TcpStream};
use tracing::{error, info, trace};
use crate::protocols::{Protocol, ProtocolConnector, ProtocolListener, ProtocolMessage};

pub struct TcpProtocol {
    socket: TcpStream
}

#[async_trait]
impl ProtocolListener for TcpProtocol {
    async fn listen(ip: &str, port: u16) -> anyhow::Result<()> {
        let listener = TcpListener::bind(format!("{ip}:{port}")).await?;

        loop {
            let (mut socket, addr) = listener.accept().await?;
            info!("Accepted connection from: {}", addr);

            tokio::spawn(async move {
                let (mut reader, mut writer) = socket.split();

                match io::copy(&mut reader, &mut writer).await {
                    Ok(bytes_copied) => {
                        trace!("Copied {} bytes for: {}", bytes_copied, addr);
                    }
                    Err(e) => {
                        error!("Failed to copy data for: {}; error: {:?}", addr, e);
                    }
                }

                info!("Connection closed for: {}", addr);
            });
        }
    }
}

#[async_trait]
impl ProtocolConnector for TcpProtocol {
    async fn connect(ip: &str, port: u16, _local_ip: &str, _local_port: u16)
                     -> anyhow::Result<Box<dyn Protocol>> {
        let stream = TcpStream::connect(format!("{}:{}", ip, port)).await?;
        Ok(Box::new(TcpProtocol { socket: stream }))
    }
}

#[async_trait]
impl Protocol for TcpProtocol {
    async fn disconnect(&mut self) {
        match self.socket.shutdown().await{
            Ok(_) => {
                info!("Socket shutdown");
            },
            Err(e) => {
                error!("Failed to shutdown socket: {:?}", e);
            }
        }
    }

    async fn send(&mut self, message: &ProtocolMessage) -> anyhow::Result<()> {
        self.socket.write(&message.to_binary()?).await?;
        self.socket.flush().await?;
        Ok(())
    }

    async fn receive(&mut self) -> anyhow::Result<ProtocolMessage> {
        let mut buf = [0; 65535];
        let size = self.socket.read(&mut buf).await?;
        trace!("{:?} bytes received", size);
        let message = ProtocolMessage::from_binary(&buf[..size])?;
        Ok(message)
    }
}