import argparse
import getpass
import os
import time
import paramiko

# Local module imports
from . import scenario
from . import emulation
from . import results

REMOTE_WORK_DIR = "/tmp/network_protocol_tester"
REMOTE_BIN = f"{REMOTE_WORK_DIR}/target/release/network_protocol_tester"
GIT_URL = "https://github.com/VALERE91/NetworkProtocolTester.git"

def get_ssh_connection(host_info):
    """Creates and returns a connected SSH client."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key_path = os.path.expanduser(host_info['private_key'])
    ssh.connect(host_info['host'], username=host_info.get('username', 'root'), key_filename=key_path)
    return ssh

def prepare_host(host_info, sudo_password):
    """Installs dependencies and builds the project on the remote host."""
    host = host_info['host']
    print(f"[*] Preparing host: {host}")

    # Define all commands in order
    # Format: (command_string, human_readable_description)
    setup_steps = [
        # Sudo commands (Require Password)
        ("sudo -S apt-get update", "Updating apt cache"),
        ("sudo -S apt-get install -y git iproute2 build-essential curl", "Installing system dependencies"),

        # User-space commands (No Password)
        ("curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y", "Installing Rust toolchain"),

        # Git Logic (Check if exists -> Pull, Else -> Clone)
        (f"if [ -d {REMOTE_WORK_DIR}/.git ]; then "
         f"echo 'Updating...'; cd {REMOTE_WORK_DIR} && git pull; "
         f"else "
         f"echo 'Cloning...'; rm -rf {REMOTE_WORK_DIR}; git clone {GIT_URL} {REMOTE_WORK_DIR}; "
         f"fi", "Syncing repository"),

        # Build Project
        (f"cd {REMOTE_WORK_DIR} && $HOME/.cargo/bin/cargo build --release", "Building project release")
    ]

    try:
        with get_ssh_connection(host_info) as ssh:
            for cmd, desc in setup_steps:
                print(f"[*] {host}: {desc}...")

                stdin, stdout, stderr = ssh.exec_command(cmd)

                # Only write password if the command actually needs it
                if cmd.strip().startswith("sudo -S"):
                    stdin.write(sudo_password + '\n')
                    stdin.flush()

                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    error_log = stderr.read().decode().strip()
                    raise Exception(f"Step '{desc}' failed: {error_log}")

            print(f"[*] Host {host} is fully prepared.")

    except Exception as e:
        print(f"[!] Failed to prepare host {host}: {e}")

def execute_test_cycle(scenario_data, results_dir):
    """Main execution loop."""

    # Provision all hosts
    all_hosts = scenario_data['servers'] + [scenario_data['client']]
    passwords = {} # Cache passwords per user/host if needed, or ask once.

    # Simple password strategy: Ask once per unique user/host combo
    for host in all_hosts:
        key = f"{host.get('username', 'root')}@{host['host']}"
        if key not in passwords:
            passwords[key] = getpass.getpass(f"Enter sudo password for {key}: ")
        prepare_host(host, passwords[key])

    client_cfg = scen_data['client']

    # Run Tests
    for protocol in scenario_data['protocols']:
        print(f"\n=== Starting Protocol Series: {protocol} ===")
        local_proto_dir = results.prepare_directories(results_dir, protocol)

        for server_cfg in scenario_data['servers']:
            srv_pass = passwords[f"{server_cfg.get('username', 'root')}@{server_cfg['host']}"]

            for emu in server_cfg.get('emulations', [{'name': 'default'}]):
                print(f"\n--- Test: {server_cfg['name']} | Mode: {emu['name']} ---")

                server_ssh = None
                client_ssh = None

                try:
                    server_ssh = get_ssh_connection(server_cfg)
                    client_ssh = get_ssh_connection(client_cfg)
                    interface = emu.get('interface', 'eth0')

                    # Network Emulation
                    emulation.clean_emulation(server_ssh, interface, srv_pass)
                    emulation.apply_emulation(server_ssh, interface, emu.get('latency'), emu.get('drop'), srv_pass)

                    # Start Server
                    port = 12345
                    print(f"[*] Starting server on {server_cfg['host']}")
                    server_ssh.exec_command(f"nohup {REMOTE_BIN} {protocol} server --port {port} > /dev/null 2>&1 &")
                    time.sleep(2) # Wait for server bind

                    # Start Client
                    remote_json = "/tmp/results.json"
                    # Construct client command
                    cmd_parts = [
                        REMOTE_BIN, protocol, "client",
                        "--server", server_cfg['host'],
                        "--port", str(port),
                        "--padding", str(scenario_data['padding']),
                        "--json", remote_json
                    ]
                    # Add optionals
                    if 'test_duration' in scenario_data: cmd_parts += ["--test-duration", str(scen_data['test_duration'])]
                    if 'reliable_freq' in scenario_data: cmd_parts += ["--reliable-freq", str(scen_data['reliable_freq'])]
                    if 'unreliable_freq' in scenario_data: cmd_parts += ["--unreliable-freq", str(scen_data['unreliable_freq'])]

                    print(f"[*] Starting client on {client_cfg['host']}")
                    stdin, stdout, stderr = client_ssh.exec_command(" ".join(cmd_parts))

                    if stdout.channel.recv_exit_status() != 0:
                        print(f"[!] Client Error: {stderr.read().decode()}")
                    else:
                        # Collect Results
                        local_file = os.path.join(local_proto_dir, f"{server_cfg['name']}_{emu['name']}.json")
                        results.download_file(client_ssh, remote_json, local_file)

                except Exception as e:
                    print(f"[!] Exception during test: {e}")

                finally:
                    # Teardown
                    if server_ssh:
                        emulation.clean_emulation(server_ssh, interface, srv_pass)
                        server_ssh.exec_command("pkill -f network_protocol_tester")
                        server_ssh.close()
                    if client_ssh:
                        client_ssh.close()

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Network Protocol Tester Executor")
    parser.add_argument("file_path", help="Path to scenario.json")
    parser.add_argument("--results-dir", default="results", help="Local results directory")
    args = parser.parse_args()

    try:
        scen_data = scenario.parse(args.file_path)
        execute_test_cycle(scen_data, args.results_dir)
    except Exception as e:
        print(f"Fatal Error: {e}")