import boto3
import os
import time
import uuid

# Constants
# We filter for the official Ubuntu 22.04 LTS image (Jammy Jellyfish)
AMI_FILTER_NAME = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"
AMI_OWNER_ID = "099720109477"  # Canonical (Ubuntu creator) ID
SSH_PORT = 22
TEST_PORT = 12345
TEMP_KEY_DIR = "/tmp/network_protocol_tester_keys"

class CloudProvisioner:
    def __init__(self, region_name):
        self.ec2 = boto3.client('ec2', region_name=region_name)
        self.ec2_resource = boto3.resource('ec2', region_name=region_name)
        self.region = region_name
        self.run_id = str(uuid.uuid4())[:8]
        self.key_name = f"net-test-key-{self.run_id}"
        self.sg_name = f"net-test-sg-{self.run_id}"
        self.instance_id = None
        self.key_path = None

        os.makedirs(TEMP_KEY_DIR, exist_ok=True)

    def _get_latest_ubuntu_ami(self):
        """Finds the latest Ubuntu 22.04 AMI in the specific region."""
        images = self.ec2.describe_images(
            Filters=[
                {'Name': 'name', 'Values': [AMI_FILTER_NAME]},
                {'Name': 'architecture', 'Values': ['x86_64']},
                {'Name': 'owner-id', 'Values': [AMI_OWNER_ID]}
            ]
        )
        # Sort by creation date descending
        sorted_images = sorted(images['Images'], key=lambda x: x['CreationDate'], reverse=True)
        if not sorted_images:
            raise Exception(f"No Ubuntu AMI found in region {self.region}")
        return sorted_images[0]['ImageId']

    def _create_key_pair(self):
        """Creates a temporary SSH key pair for this session."""
        print(f"[*] Creating temporary SSH key: {self.key_name}")
        key_pair = self.ec2.create_key_pair(KeyName=self.key_name)

        self.key_path = os.path.join(TEMP_KEY_DIR, f"{self.key_name}.pem")
        with open(self.key_path, "w") as f:
            f.write(key_pair['KeyMaterial'])

        # chmod 400 is required for SSH keys
        os.chmod(self.key_path, 0o400)
        return self.key_name

    def _create_security_group(self):
        """Creates a security group allowing SSH and the Test Port."""
        print(f"[*] Creating security group: {self.sg_name}")
        # Get default VPC
        vpcs = self.ec2.describe_vpcs(Filters=[{'Name': 'isDefault', 'Values': ['true']}])
        vpc_id = vpcs['Vpcs'][0]['VpcId']

        sg = self.ec2.create_security_group(
            GroupName=self.sg_name,
            Description="Temporary SG for Network Protocol Tester",
            VpcId=vpc_id
        )
        sg_id = sg['GroupId']

        # Allow inbound rules
        self.ec2.authorize_security_group_ingress(
            GroupId=sg_id,
            IpPermissions=[
                # SSH
                {'IpProtocol': 'tcp', 'FromPort': SSH_PORT, 'ToPort': SSH_PORT, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]},
                # Application Port
                {'IpProtocol': 'tcp', 'FromPort': TEST_PORT, 'ToPort': TEST_PORT, 'IpRanges': [{'CidrIp': '0.0.0.0/0'}]}
            ]
        )
        return sg_id

    def provision(self, instance_type="t3.micro"):
        """
        Orchestrates the creation of the VM.
        Returns a host_info dictionary compatible with the executor.
        """
        try:
            ami_id = self._get_latest_ubuntu_ami()
            self._create_key_pair()
            sg_id = self._create_security_group()

            print(f"[*] Launching {instance_type} in {self.region} (AMI: {ami_id})...")
            instances = self.ec2_resource.create_instances(
                ImageId=ami_id,
                MinCount=1,
                MaxCount=1,
                InstanceType=instance_type,
                KeyName=self.key_name,
                SecurityGroupIds=[sg_id],
                TagSpecifications=[{
                    'ResourceType': 'instance',
                    'Tags': [{'Key': 'Name', 'Value': f'NetTester-{self.run_id}'}]
                }]
            )
            instance = instances[0]
            self.instance_id = instance.id

            print(f"[*] Waiting for instance {self.instance_id} to be running...")
            instance.wait_until_running()

            # Reload to get public IP
            instance.reload()
            public_ip = instance.public_ip_address
            print(f"[*] Instance Running at {public_ip}. Waiting for SSH to become available...")

            # Simple TCP wait (SSH takes a moment after boot to actually accept connections)
            self._wait_for_ssh(public_ip)

            return {
                'host': public_ip,
                'username': 'ubuntu', # Standard for AWS Ubuntu AMIs
                'private_key': self.key_path,
                'is_cloud': True, # Flag to disable emulation in main.py
                'instance_id': self.instance_id,
                'region': self.region
            }

        except Exception as e:
            print(f"[!] Provisioning failed: {e}")
            self.teardown()
            raise e

    def _wait_for_ssh(self, ip, timeout=60):
        """Blocks until port 22 is open."""
        import socket
        start = time.time()
        while time.time() - start < timeout:
            try:
                with socket.create_connection((ip, 22), timeout=2):
                    return
            except (socket.timeout, ConnectionRefusedError):
                time.sleep(2)
        print("[!] Warning: SSH socket wait timed out, proceeding anyway...")

    def teardown(self):
        """Destroys the instance, keys, and security groups."""
        print(f"[*] Tearing down cloud resources for run {self.run_id}...")

        if self.instance_id:
            print(f" -> Terminating instance {self.instance_id}")
            self.ec2.terminate_instances(InstanceIds=[self.instance_id])
            # We must wait for termination before deleting SGs
            waiter = self.ec2.get_waiter('instance_terminated')
            waiter.wait(InstanceIds=[self.instance_id])

        if self.key_name:
            print(f" -> Deleting key pair {self.key_name}")
            self.ec2.delete_key_pair(KeyName=self.key_name)
            if self.key_path and os.path.exists(self.key_path):
                os.remove(self.key_path)

        if self.sg_name:
            print(f" -> Deleting security group {self.sg_name}")
            try:
                self.ec2.delete_security_group(GroupName=self.sg_name)
            except Exception as e:
                print(f"[!] Could not delete SG (likely dependency): {e}")