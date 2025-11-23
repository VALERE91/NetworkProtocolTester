import os
from scp import SCPClient

def prepare_directories(base_dir, protocol):
    """
    Creates the necessary local directories for storing results.
    """
    protocol_dir = os.path.join(base_dir, protocol)
    os.makedirs(protocol_dir, exist_ok=True)
    return protocol_dir

def download_file(ssh_client, remote_path, local_path):
    """
    Downloads a file from the remote host using SCP.
    """
    try:
        with SCPClient(ssh_client.get_transport()) as scp:
            scp.get(remote_path, local_path)
            print(f"-> Results saved to: {local_path}")
    except Exception as e:
        print(f"[!] Failed to download results: {e}")