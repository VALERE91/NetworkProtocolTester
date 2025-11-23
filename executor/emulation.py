import time

def clean_emulation(ssh_client, interface, password):
    """
    Removes any existing network emulation rules (netem) from the interface.
    Failures (e.g., if no rule exists) are ignored to ensure idempotency.
    """
    # We allow this to 'fail' (exit code 2) because there might not be a rule to delete.
    command = f"sudo -S tc qdisc del dev {interface} root"
    stdin, _, _ = ssh_client.exec_command(command)
    stdin.write(password + '\n')
    stdin.flush()

    # Give the kernel a moment to release the lock
    time.sleep(0.5)

def apply_emulation(ssh_client, interface, latency, drop, password):
    """
    Applies network emulation (latency and packet loss).
    """
    if latency is None or drop is None:
        return

    print(f"-> Applying emulation: {latency}ms latency, {drop}% drop on {interface}")

    # FIX: Added 'limit 100000' to prevent TCP buffer overflow during high latency
    command = f"sudo -S tc qdisc add dev {interface} root netem limit 100000 latency {latency}ms loss {drop}%"

    stdin, stdout, stderr = ssh_client.exec_command(command)
    stdin.write(password + '\n')
    stdin.flush()

    exit_status = stdout.channel.recv_exit_status()
    if exit_status != 0:
        error_msg = stderr.read().decode().strip()
        print(f"[!] Error applying netem: {error_msg}")