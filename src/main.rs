use std::fs::File;
use std::time::Duration;
use clap::{Parser, Subcommand};
use indicatif::{ProgressBar, ProgressStyle};
use rustls::crypto;
use tokio::time::Instant;
use tracing::{error, info};
use crate::protocols::{start_client, start_server, ClientConfig, ProtocolMessage, ServerConfig};
use crate::results::TestResult;

mod protocols;
mod results;

#[derive(Parser)]
#[command(name = "NetworkProtocolTester")]
#[command(version = "1.0")]
#[command(about = "Test the latency of various protocols", long_about = None)]
struct Cli{
    /// The protocol to use during the test.
    protocol: protocols::ProtocolType,

    #[command(subcommand)]
    /// The mode to run the test in (i.e. server or client)
    mode: TestingModes,
}
#[derive(Subcommand)]
enum TestingModes {
    Server {
        #[arg(short, long)]
        /// The interface to listen on.
        interface: Option<String>,
        #[arg(short, long)]
        /// The port to listen on.
        port: u16,
    },
    Client {
        #[arg(short, long)]
        /// The host to connect to.
        server: String,
        #[arg(short, long)]
        /// The port to connect to.
        port: u16,
        #[arg(long, default_value = "0.0.0.0")]
        /// The interface to bind on.
        interface: String,
        #[arg(long, default_value_t = 0)]
        /// The port to bind on.
        local_port: u16,
        #[arg(long, default_value_t = 1024)]
        /// The padding added to the protocol packet.
        padding: u16,
        #[arg(long, default_value_t = 10)]
        /// The time this test will run for in seconds.
        test_duration: u64,
        #[arg(long, default_value_t = 10)]
        /// The frequency (in Hz) to send reliable packets at.
        reliable_freq: u64,
        #[arg(long, default_value_t = 100)]
        /// The frequency (in Hz) to send unreliable packets at.
        unreliable_freq: u64,
        #[arg(long)]
        /// Output the results in JSON format in a file at this path.
        json: Option<std::path::PathBuf>,
    }
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt::init();
    crypto::ring::default_provider()
        .install_default()
        .expect("Failed to install rustls crypto provider");

    let cli = Cli::parse();

    info!("Starting protocol tester with protocol: {}", cli.protocol);

    match cli.mode {
        TestingModes::Server { interface, port } => {
            info!("Starting server on interface: {:?}, port: {:?}", interface, port);
            start_server(ServerConfig{
                protocol: cli.protocol,
                ip: interface.clone(),
                port,
            }).await;
        }
        TestingModes::Client {
            server,
            port,
            interface,
            local_port,
            padding,
            test_duration,
            reliable_freq,
            unreliable_freq,
            json } => {
            info!("Starting client to host: {}, port: {}", server, port);
            let mut client = start_client(ClientConfig{
                protocol: cli.protocol,
                host_port: port,
                host_ip: server.clone(),
                local_port: Some(local_port),
                local_ip: Some(interface),
            }).await?;
            info!("Client connected to server");

            let mut results = TestResult::new(cli.protocol);

            let padding = padding;

            let mut reliable_interval = tokio::time::interval(Duration::from_micros(1_000_000 / reliable_freq));
            let mut unreliable_interval = tokio::time::interval(Duration::from_micros(1_000_000 / unreliable_freq));
            let end_time = Instant::now() + Duration::from_secs(test_duration);

            let steps = 100;
            let pb = ProgressBar::new(steps);
            let pb_style = ProgressStyle::default_bar().template("{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] ({eta})")?;
            pb.set_style(pb_style);
            let interval_progress = Duration::from_micros((test_duration * 1_000_000) / steps);
            let mut progress_interval = tokio::time::interval(interval_progress);

            while Instant::now() < end_time {
                tokio::select! {
                    _ = reliable_interval.tick() => {
                        let message = ProtocolMessage::new(padding, true);
                        client.send(&message).await?;
                        results.packet_sent(true);
                    }
                    _ = unreliable_interval.tick() => {
                        let message = ProtocolMessage::new(padding, false);
                        client.send(&message).await?;
                        results.packet_sent(false);
                    },
                    _ = progress_interval.tick() => {
                        pb.inc(1);
                    },
                    message = client.receive() => {
                        if let Ok(message) = message {
                            results.add_packet(&message);
                        }else{
                            error!("Failed to receive message");
                            break;
                        }
                    }
                }
            }

            info!("Test duration of {}s finished. Waiting 10s for in-flight packets.", test_duration);

            let pb = ProgressBar::new(steps);
            let pb_style = ProgressStyle::default_bar().template("{spinner:.green} [{elapsed_precise}] [{bar:40.cyan/blue}] ({eta})")?;
            pb.set_style(pb_style);

            let end_time = Instant::now() + Duration::from_secs(10);
            while Instant::now() < end_time {
                tokio::select! {
                    _ = progress_interval.tick() => {
                        pb.inc(1);
                    },
                    message = client.receive() => {
                        if let Ok(message) = message {
                            results.add_packet(&message);
                        }else{
                            error!("Failed to receive message");
                            break;
                        }
                    }
                }
            }

            client.disconnect().await;
            info!("Client disconnected");

            results.finalize();

            if let Some(json_path) = json {
                if !json_path.exists() {
                    File::create(json_path.clone())?;    
                }
                
                if json_path.exists() {
                    info!("Writing results to file: {:?}", json_path);
                    let json_results = serde_json::to_string_pretty(&results)?;
                    std::fs::write(json_path, json_results)?;
                } else {
                    error!("File does not exist: {:?}", json_path);
                }
            }
        }
    }
    Ok(())
}