import json
import argparse
import os
import time
import paramiko
from scp import SCPClient

def parse_scenario(file_path):
    """
    Parses the scenario file.
    :param file_path: The path to the scenario file.
    :return: The parsed scenario file.
    """
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found: {file_path}")

    with open(file_path, 'r') as f:
        return json.load(f)

def prepare_host(host_info):
    """
    Prepares a remote host by installing dependencies and building the project.
    :param host_info: A dictionary containing host, username, and private_key.
    """
    host = host_info['host']
    username = host_info.get('username', 'root') # Assuming 'root' if not specified
    private_key_path = host_info['private_key']

    print(f"Preparing host: {host}")

    try:
        with paramiko.SSHClient() as ssh:
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            ssh.connect(host, username=username, key_filename=os.path.expanduser(private_key_path))

            print(f"Installing dependencies on {host}...")
            # Install Git and Rust
            commands = [
                "apt-get update",
                "apt-get install -y git",
                "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y"
            ]
            for command in commands:
                print(f"Executing: {command}")
                stdin, stdout, stderr = ssh.exec_command(command)
                exit_status = stdout.channel.recv_exit_status()
                if exit_status != 0:
                    print(f"Error executing command: {command}")
                    print(stderr.read().decode())
                    return

            # For simplicity, we're assuming the current working directory is the git repo.
            # We will copy the project over instead of cloning.
            project_root = os.getcwd()
            remote_path = "/tmp/network_protocol_tester"

            print(f"Creating remote directory {remote_path} on {host}")
            ssh.exec_command(f"mkdir -p {remote_path}")

            print(f"Copying project files to {host}:{remote_path}...")
            with SCPClient(ssh.get_transport()) as scp:
                scp.put(project_root, recursive=True, remote_path=remote_path)

            print(f"Building project on {host}...")
            build_command = f"cd {remote_path} && $HOME/.cargo/bin/cargo build --release"
            print(f"Executing: {build_command}")
            stdin, stdout, stderr = ssh.exec_command(build_command)
            exit_status = stdout.channel.recv_exit_status()
            if exit_status != 0:
                print(f"Error building project on {host}")
                print(stderr.read().decode())
                return

            print(f"Host {host} prepared successfully.")

    except Exception as e:
        print(f"Failed to prepare host {host}: {e}")

def run_test(scenario, results_dir):
    """
    Runs the test based on the scenario.
    :param scenario: The parsed scenario file.
    :param results_dir: The directory to store the results.
    """

    # Prepare all hosts
    all_hosts = scenario['servers'] + [scenario['client']]
    for host_info in all_hosts:
        # Assuming the key path might be relative or need expansion
        host_info['private_key'] = os.path.expanduser(host_info['private_key'])
        prepare_host(host_info)

    os.makedirs(results_dir, exist_ok=True)
    remote_executable_path = "/tmp/network_protocol_tester/target/release/network_protocol_tester"

    for protocol in scenario['protocols']:
        protocol_dir = os.path.join(results_dir, protocol)
        os.makedirs(protocol_dir, exist_ok=True)

        for server_info in scenario['servers']:
            server_name = server_info['name']
            emulations = server_info.get('emulations', [{'name': 'default'}])

            for emulation in emulations:
                emulation_name = emulation['name']
                print(f"Testing protocol: {protocol} on server: {server_name} with emulation: {emulation_name}")

                port = 12345

                server_ssh = None
                client_ssh = None

                try:
                    # Connect to server
                    server_ssh = paramiko.SSHClient()
                    server_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    server_ssh.connect(server_info['host'], username=server_info.get('username', 'root'), key_filename=server_info['private_key'])

                    # Connect to client
                    client_info = scenario['client']
                    client_ssh = paramiko.SSHClient()
                    client_ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    client_ssh.connect(client_info['host'], username=client_info.get('username', 'root'), key_filename=client_info['private_key'])

                    # Start the server remotely
                    server_command = f"nohup {remote_executable_path} {protocol} server --port {port} > /dev/null 2>&1 &"
                    print(f"Starting server on {server_info['host']}: {server_command}")
                    server_ssh.exec_command(server_command)
                    time.sleep(2)

                    # Start the client remotely
                    remote_json_path = "/tmp/results.json"
                    client_command_parts = [
                        remote_executable_path,
                        protocol,
                        "client",
                        "--server", server_info['host'],
                        "--port", str(port),
                        "--padding", str(scenario['padding']),
                        "--json", remote_json_path,
                    ]

                    if 'test_duration' in scenario:
                        client_command_parts.extend(["--test-duration", str(scenario['test_duration'])])
                    if 'reliable_freq' in scenario:
                        client_command_parts.extend(["--reliable-freq", str(scenario['reliable_freq'])])
                    if 'unreliable_freq' in scenario:
                        client_command_parts.extend(["--unreliable-freq", str(scenario['unreliable_freq'])])

                    client_command = " ".join(client_command_parts)
                    print(f"Starting client on {client_info['host']}: {client_command}")
                    stdin, stdout, stderr = client_ssh.exec_command(client_command)
                    exit_status = stdout.channel.recv_exit_status()

                    if exit_status != 0:
                        print(f"Client execution failed on {client_info['host']} with exit code {exit_status}")
                        print(stderr.read().decode())
                    else:
                        # Download the results
                        local_json_path = os.path.join(protocol_dir, f"{server_name}_{emulation_name}.json")
                        print(f"Downloading results from {client_info['host']}:{remote_json_path} to {local_json_path}")
                        with SCPClient(client_ssh.get_transport()) as scp:
                            scp.get(remote_json_path, local_json_path)

                except Exception as e:
                    print(f"An error occurred during test execution: {e}")
                finally:
                    # Clean up the server process and close connections
                    if server_ssh:
                        print(f"Terminating server on {server_info['host']}...")
                        # Using pkill is a simple way to ensure the process is stopped.
                        server_ssh.exec_command("pkill -f network_protocol_tester")
                        server_ssh.close()
                    if client_ssh:
                        client_ssh.close()

                print(f"Finished testing protocol: {protocol} on server: {server_name} with emulation: {emulation_name}\n")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Parses a scenario file.")
    parser.add_argument("file_path", help="The path to the scenario file.")
    parser.add_argument("--results-dir", default="results", help="The directory to store the results.")
    args = parser.parse_args()

    try:
        scenario = parse_scenario(args.file_path)
        run_test(scenario, args.results_dir)
    except FileNotFoundError as e:
        print(e)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
