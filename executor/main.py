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
REPO_OWNER = "VALERE91"
REPO_NAME = "NetworkProtocolTester"
GIT_URL = "https://github.com/VALERE91/NetworkProtocolTester.git"

def get_ssh_connection(host_info):
    """Creates and returns a connected SSH client."""
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    key_path = os.path.expanduser(host_info['private_key'])
    ssh.connect(host_info['host'], username=host_info.get('username', 'root'), key_filename=key_path)
    return ssh

def resolve_binary_url(ssh, version):
    """
    Detects remote OS/Arch and returns the correct GitHub Release URL.
    """
    # Detect OS
    stdin, stdout, stderr = ssh.exec_command("uname -s")
    os_name = stdout.read().decode().strip().lower()

    # Detect Arch
    stdin, stdout, stderr = ssh.exec_command("uname -m")
    arch = stdout.read().decode().strip().lower()

    # Map to Artifact Name (Matches the GH Actions naming convention)
    # Naming convention: network_protocol_tester-<platform>[-<arch>]
    # Linux x86_64 -> network_protocol_tester-linux-x86_64
    # MacOS ARM64  -> network_protocol_tester-macos-arm64

    suffix = ""
    if "linux" in os_name:
        if arch == "x86_64":
            suffix = "linux-x86_64"
        else:
            raise Exception(f"Unsupported Linux architecture: {arch}. Release only has x86_64.")
    elif "darwin" in os_name: # macOS
        if arch == "arm64":
            suffix = "macos-arm64"
        elif arch == "x86_64":
            suffix = "macos-intel" # Assuming you named it this in GH Actions
        else:
            raise Exception(f"Unsupported macOS architecture: {arch}")
    else:
        raise Exception(f"Unsupported Remote OS: {os_name}")

    binary_name = f"network_protocol_tester-{suffix}"

    # Construct URL
    url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/download/{version}/{binary_name}"
    return url

def prepare_host(host_info, sudo_password, version):
    """
    Prepares host by downloading the binary directly from GitHub.
    """
    host = host_info['host']
    print(f"[*] Preparing host: {host} (Target: {version})")

    try:
        with get_ssh_connection(host_info) as ssh:
            # 1. Determine Download URL
            download_url = resolve_binary_url(ssh, version)
            print(f" -> Detected platform. Downloading from: {download_url}")

            # 2. Install Runtime Dependencies (curl + iproute2 for tc)
            # Check if apt-get exists (Linux) to install dependencies
            stdin, stdout, stderr = ssh.exec_command("which apt-get")
            if stdout.channel.recv_exit_status() == 0:
                print(f" -> Installing dependencies on {host}...")
                cmd = "sudo -S apt-get update && sudo -S apt-get install -y iproute2 curl"
                stdin, stdout, stderr = ssh.exec_command(cmd)
                stdin.write(sudo_password + '\n')
                stdin.flush()
                if stdout.channel.recv_exit_status() != 0:
                    print(f"[!] Warning: Dep install failed: {stderr.read().decode()}")

            # 3. Setup Directory
            ssh.exec_command(f"mkdir -p {REMOTE_WORK_DIR}")

            # 4. Download Binary
            # -L follows redirects, -o saves to specific path
            download_cmd = f"curl -L -o {REMOTE_BIN} {download_url}"
            stdin, stdout, stderr = ssh.exec_command(download_cmd)
            if stdout.channel.recv_exit_status() != 0:
                error = stderr.read().decode()
                if "404" in error or "404" in stdout.read().decode():
                    raise Exception(f"Release binary not found on GitHub. Check version {version} and URL.")
                raise Exception(f"Download failed: {error}")

            # 5. Make Executable
            ssh.exec_command(f"chmod +x {REMOTE_BIN}")
            print(f"[*] Host {host} ready.")

    except Exception as e:
        print(f"[!] Failed to prepare host {host}: {e}")
        raise e

def execute_test_cycle(scenario_data, results_dir):
    """Main execution loop."""

    # Provision all hosts
    all_hosts = scenario_data['servers'] + [scenario_data['client']]
    passwords = {} # Cache passwords per user/host if needed, or ask once.
    version = scen_data.get('release_version')

    if not version:
        raise ValueError("Scenario file is missing 'release_version' field (e.g. 'v1.0.1')")

    # Simple password strategy: Ask once per unique user/host combo
    for host in all_hosts:
        key = f"{host.get('username', 'root')}@{host['host']}"
        if key not in passwords:
            passwords[key] = getpass.getpass(f"Enter sudo password for {key}: ")
        prepare_host(host, passwords[key], version)

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