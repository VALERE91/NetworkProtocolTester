import json
import argparse
import os
import subprocess
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

    print("Building cargo executable...")
    subprocess.run(["cargo", "build", "--release"], check=True)
    executable_path = "target/release/network_protocol_tester"
    os.makedirs(results_dir, exist_ok=True)

    for protocol in scenario['protocols']:
        protocol_dir = os.path.join(results_dir, protocol)
        os.makedirs(protocol_dir, exist_ok=True)

        for server_info in scenario['servers']:
            server_name = server_info['name']
            emulations = server_info.get('emulations', [{'name': 'default'}])

            for emulation in emulations:
                emulation_name = emulation['name']
                print(f"Testing protocol: {protocol} on server: {server_name} with emulation: {emulation_name}")

                # For simplicity, we'll use a fixed port.
                # In a real-world scenario, you might want to dynamically allocate this.
                port = 12345

                # Start the server
                server_command = [
                    executable_path,
                    protocol,
                    "server",
                    "--port", str(port)
                ]
                print(f"Starting server: {' '.join(server_command)}")
                server_process = subprocess.Popen(server_command)

                # Give the server a moment to start
                time.sleep(2)

                # Start the client
                json_output_path = os.path.join(protocol_dir, f"{server_name}_{emulation_name}.json")
                client_command = [
                    executable_path,
                    protocol,
                    "client",
                    "--server", server_info['host'],
                    "--port", str(port),
                    "--padding", str(scenario['padding']),
                    "--json", json_output_path,
                ]

                if 'test_duration' in scenario:
                    client_command.extend(["--test-duration", str(scenario['test_duration'])])
                if 'reliable_freq' in scenario:
                    client_command.extend(["--reliable-freq", str(scenario['reliable_freq'])])
                if 'unreliable_freq' in scenario:
                    client_command.extend(["--unreliable-freq", str(scenario['unreliable_freq'])])
                print(f"Starting client: {' '.join(client_command)}")
                subprocess.run(client_command, check=True)

                # Clean up the server process
                print("Terminating server...")
                server_process.terminate()
                server_process.wait()

                print(f"Finished testing protocol: {protocol} on server: {server_name} with emulation: {emulation_name}\n")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Parses a scenario file.")
    parser.add_argument("file_path", help="The path to the scenario file.")
    parser.add_argument("--results-dir", default="results", help="The directory to store the results.")
    args = parser.parse_args()

    try:
        run_test(parse_scenario(args.file_path), args.results_dir)
    except FileNotFoundError as e:
        print(e)
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON: {e}")
