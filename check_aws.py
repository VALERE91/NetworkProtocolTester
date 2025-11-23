import boto3
from botocore.exceptions import NoCredentialsError, ClientError

def verify_aws_setup():
    print("--- AWS Credential & Permission Check ---")

    try:
        # Identity Check (Who am I?)
        sts = boto3.client('sts')
        identity = sts.get_caller_identity()
        print(f"✅ Credentials are VALID.")
        print(f"   User ARN: {identity['Arn']}")
        print(f"   Account ID: {identity['Account']}")

        # Permission Check (Can I touch EC2?)
        # We try to list instances. If this fails, your user lacks permissions.
        ec2 = boto3.client('ec2')
        ec2.describe_instances(MaxResults=5)
        print(f"✅ EC2 Permissions are VALID.")
        print("   (You are ready to run the network tester.)")

    except NoCredentialsError:
        print("❌ ERROR: No credentials found.")
        print("   Solution: Run 'aws configure' in your terminal.")

    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'UnauthorizedOperation':
            print("❌ ERROR: Credentials valid, but PERMISSION DENIED.")
            print("   Solution: Go to IAM Console and attach 'AmazonEC2FullAccess' to this user.")
        else:
            print(f"❌ ERROR: AWS API returned an error: {e}")

    except Exception as e:
        print(f"❌ UNEXPECTED ERROR: {e}")

if __name__ == "__main__":
    verify_aws_setup()