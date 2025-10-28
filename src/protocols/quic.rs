use std::fmt::{Debug, Formatter};
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use std::net::SocketAddr;
use std::sync::Arc;
use async_trait::async_trait;
use quinn::{ClientConfig, Endpoint, ServerConfig};
use quinn::crypto::rustls::QuicClientConfig;
use rustls::{DigitallySignedStruct, Error, SignatureScheme};
use rustls::pki_types::{CertificateDer, PrivatePkcs8KeyDer, ServerName, UnixTime};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::mpsc;
use tracing::{error, info, trace};
use crate::protocols::{Protocol, ProtocolConnector, ProtocolListener, ProtocolMessage};

pub struct QuicProtocol {
    sending_queue_tx: mpsc::Sender<ProtocolMessage>,
    receiving_queue_rx: mpsc::Receiver<ProtocolMessage>,
}

#[async_trait]
impl ProtocolListener for QuicProtocol {
    async fn listen(ip: &str, port: u16) -> anyhow::Result<()> {
        let server_config = configure_server()?;
        let endpoint = Endpoint::server(server_config, format!("{ip}:{port}").parse()?)?;
        info!("QUIC server listening on {}:{}", ip, port);

        while let Some(conn) = endpoint.accept().await {
            tokio::spawn(async move {
                match conn.await {
                    Ok(connection) => {
                        info!("Accepted QUIC connection from: {}", connection.remote_address());
                        tokio::spawn(handle_connection(connection));
                    }
                    Err(e) => {
                        error!("Failed to accept QUIC connection: {}", e);
                    }
                }
            });
        }
        Ok(())
    }
}

async fn handle_connection(connection: quinn::Connection) {
    match connection.accept_bi().await {
        Ok((mut send, mut recv)) => {
            tokio::spawn(async move {
                let mut buf = vec![0; 65535];
                loop {
                    tokio::select! {
                        result = recv.read(&mut buf) => {
                            match result {
                                Ok(Some(len)) => {
                                    trace!("Received {} bytes", len);
                                    if let Err(e) = send.write_all(&buf[..len]).await {
                                        error!("Failed to write to stream: {}", e);
                                        break;
                                    }
                                    if let Err(e) = send.flush().await {
                                        error!("Failed to flush stream: {}", e);
                                        break;
                                    }
                                }
                                Ok(None) => {
                                    break;
                                }
                                Err(e) => {
                                    error!("Failed to read from stream: {}", e);
                                    break;
                                }
                            }
                        },
                        dgram = connection.read_datagram() => {
                            if let Ok(dgram) = dgram {
                                if let Ok(message) = ProtocolMessage::from_binary(&dgram){
                                    trace!("Received unreliable message: {}", message);
                                    connection.send_datagram(dgram.into()).unwrap_or_else(|e| {
                                        error!("Failed to send datagram: {}", e);
                                    });
                                }else{
                                    error!("Failed to parse unreliable message");
                                }
                            }
                        }
                    }
                }
            });
        }
        Err(e) => {
            info!("No more streams to accept: {}", e);
        }
    }
}

#[async_trait]
impl ProtocolConnector for QuicProtocol {
    async fn connect(ip: &str, port: u16, local_ip: &str, local_port: u16)
        -> anyhow::Result<Box<dyn Protocol>> {
        let local_addr: SocketAddr = format!("{local_ip}:{local_port}").parse()?;
        let mut endpoint = Endpoint::client(local_addr)?;
        endpoint.set_default_client_config(configure_client()?);

        let server_addr: SocketAddr = format!("{ip}:{port}").parse()?;
        let connection = endpoint.connect(server_addr, "localhost")?.await?;
        info!("QUIC client connected to {}", connection.remote_address());

        let (send, recv) = connection.open_bi().await?;

        let (sending_tx, sending_rx) = mpsc::channel::<ProtocolMessage>(1024);
        let (unreliable_sending_tx, unreliable_sending_rx) = mpsc::channel::<ProtocolMessage>(1024);
        let (receiving_tx, receiving_rx) = mpsc::channel::<ProtocolMessage>(1024);

        tokio::spawn(async move {
            match send_loop(send, sending_rx, unreliable_sending_tx).await{
                Ok(_) => {
                    info!("QUIC client disconnected");
                }
                Err(e) => {
                    info!("QUIC client disconnected with error: {}", e);
                }
            }
        });
        let unreliable_receiving_tx = receiving_tx.clone();
        tokio::spawn(async move {
            match unreliable_loop(connection, unreliable_receiving_tx, unreliable_sending_rx).await {
                Ok(_) => {
                    info!("QUIC client disconnected");
                }
                Err(e) => {
                    info!("QUIC client disconnected with error: {}", e);
                }
            }
        });

        let reliable_receiving_tx = receiving_tx.clone();
        tokio::spawn(async move {
            match receiving_loop(recv, reliable_receiving_tx).await {
                Ok(_) => {
                    info!("QUIC client disconnected");
                }
                Err(e) => {
                    info!("QUIC client disconnected with error: {}", e);
                }
            }
        });

        Ok(Box::new(Self {
            sending_queue_tx: sending_tx,
            receiving_queue_rx: receiving_rx,
        }))
    }
}

async fn unreliable_loop(connection: quinn::Connection,
                         receiving_queue_tx: mpsc::Sender<ProtocolMessage>,
                         mut unreliable_sending_queue_rx: mpsc::Receiver<ProtocolMessage>) -> anyhow::Result<()> {
    loop {
        tokio::select! {
            message = unreliable_sending_queue_rx.recv() => {
                if let Some(message) = message {
                    let binary_message = message.to_binary()?;
                    trace!("Sending unreliable message: {}", binary_message.len());
                    connection.send_datagram(binary_message.into())?;
                } else {
                    info!("Unreliable sending queue closed");
                    connection.close(0u32.into(), b"done");
                    return Ok(());
                }
            },
            dgram = connection.read_datagram() => {
                if let Ok(dgram) = dgram {
                    trace!("Received unreliable message: {}", dgram.len());
                    let message = ProtocolMessage::from_binary(&dgram)?;
                    receiving_queue_tx.send(message).await?;
                }else{
                    info!("Unreliable receiving queue closed");
                    connection.close(0u32.into(), b"done");
                    return Ok(());
                }
            }
        }
    }
}

async fn send_loop(mut send: quinn::SendStream,
                   mut sending_queue_rx: mpsc::Receiver<ProtocolMessage>,
                   unreliable_queue_tx: mpsc::Sender<ProtocolMessage>) -> anyhow::Result<()> {

    while let Some(message) = sending_queue_rx.recv().await {
        if message.reliable {
            let binary_message = message.to_binary()?;
            trace!("Sending reliable message: {}", binary_message.len());
            send.write_u32(binary_message.len() as u32).await?;
            send.write_all(&binary_message).await?;
            send.flush().await?;
            continue;
        }
        unreliable_queue_tx.send(message).await?;
    }
    Ok(())
}

async fn receiving_loop(mut recv: quinn::RecvStream, sending_queue_tx: mpsc::Sender<ProtocolMessage>) -> anyhow::Result<()> {
    while let Ok(len) = recv.read_u32().await {
        trace!("Received message of length: {}", len);
        let mut buf = vec![0; len as usize];
        recv.read_exact(&mut buf).await?;
        let message = ProtocolMessage::from_binary(&buf)?;
        sending_queue_tx.send(message).await?;
    }
    Ok(())
}

#[async_trait]
impl Protocol for QuicProtocol {
    async fn disconnect(&mut self) {

    }

    async fn send(&mut self, message: &ProtocolMessage) -> anyhow::Result<()> {
        Ok(self.sending_queue_tx.send(message.clone()).await?)
    }

    async fn receive(&mut self) -> anyhow::Result<ProtocolMessage> {
        self.receiving_queue_rx.recv().await.ok_or_else(|| anyhow::anyhow!("Failed to receive message"))
    }
}

fn configure_server() -> anyhow::Result<ServerConfig> {
    info!("generating self-signed certificate");
    let cert = rcgen::generate_simple_self_signed(vec!["localhost".into()])?;
    let key = PrivatePkcs8KeyDer::from(cert.signing_key.serialize_der());
    let cert = cert.cert;
    let mut server_config = ServerConfig::with_single_cert(vec![cert.into()], key.into())?;
    let transport_config = Arc::get_mut(&mut server_config.transport)
        .ok_or_else(|| anyhow::anyhow!("Failed to get mutable transport config"))?;
    transport_config.max_concurrent_bidi_streams(10_000u32.into());

    Ok(server_config)
}

fn configure_client() -> anyhow::Result<ClientConfig> {
    struct SkipServerVerification;

    impl SkipServerVerification {
        fn new() -> Arc<Self> {
            Arc::new(Self)
        }
    }

    impl Debug for SkipServerVerification {
        fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
            f.debug_struct("SkipServerVerification").finish()
        }
    }
    impl ServerCertVerifier for SkipServerVerification {
        fn verify_server_cert(&self,
                              _end_entity: &CertificateDer<'_>,
                              _intermediates: &[CertificateDer<'_>],
                              _server_name: &ServerName<'_>,
                              _ocsp_response: &[u8],
                              _now: UnixTime) -> Result<ServerCertVerified, Error> {
            Ok(ServerCertVerified::assertion())
        }

        fn verify_tls12_signature(&self, _message: &[u8], _cert: &CertificateDer<'_>, _dss: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, Error> {
            Ok(HandshakeSignatureValid::assertion())
        }

        fn verify_tls13_signature(&self, _message: &[u8], _cert: &CertificateDer<'_>, _dss: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, Error> {
            Ok(HandshakeSignatureValid::assertion())
        }

        fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
            vec![SignatureScheme::RSA_PKCS1_SHA1,
                 SignatureScheme::ECDSA_SHA1_Legacy,
                 SignatureScheme::RSA_PKCS1_SHA256,
                 SignatureScheme::ECDSA_NISTP256_SHA256,
                 SignatureScheme::RSA_PKCS1_SHA384,
                 SignatureScheme::ECDSA_NISTP384_SHA384,
                 SignatureScheme::RSA_PKCS1_SHA512,
                 SignatureScheme::ECDSA_NISTP521_SHA512,
                 SignatureScheme::RSA_PSS_SHA256,
                 SignatureScheme::RSA_PSS_SHA384,
                 SignatureScheme::RSA_PSS_SHA512,
                 SignatureScheme::ED25519,
                 SignatureScheme::ED448,
                 SignatureScheme::ML_DSA_44,
                 SignatureScheme::ML_DSA_65,
                 SignatureScheme::ML_DSA_87]
        }
    }

    let roots = rustls::RootCertStore::empty();
    let mut client_crypto = rustls::ClientConfig::builder()
        .with_root_certificates(roots)
        .with_no_client_auth();
    client_crypto.dangerous().set_certificate_verifier(SkipServerVerification::new());
    let client_config = ClientConfig::new(Arc::new(QuicClientConfig::try_from(client_crypto)?));
    Ok(client_config)
}