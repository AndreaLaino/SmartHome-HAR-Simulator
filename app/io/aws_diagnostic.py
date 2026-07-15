"""
AWS S3 Connection Diagnostic Tool
Helps troubleshoot AWS connection and permissions issues.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import scrolledtext, messagebox
from typing import Optional

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError, PartialCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False


class AWSConnectionTester:
    """Test AWS S3 connection and permissions."""
    
    def __init__(self, access_key: Optional[str] = None,
                 secret_key: Optional[str] = None,
                 region: str = 'us-east-1'):
        self.access_key = access_key
        self.secret_key = secret_key
        self.region = region
        self.results = []
    
    def log(self, message: str, level: str = "INFO"):
        prefix = {"INFO": "ℹ️", "OK": "✓", "ERROR": "✗", "WARN": "⚠️"}.get(level, "•")
        self.results.append(f"{prefix} {message}")
    
    def test_connection(self) -> bool:
        """Test basic AWS connection."""
        self.results.clear()
        
        self.log("=== AWS S3 Connection Diagnostic ===", "INFO")
        self.log("", "INFO")
        
        # Test 1: boto3 availability
        if not BOTO3_AVAILABLE:
            self.log("boto3 is NOT installed", "ERROR")
            self.log("Install with: pip install boto3", "INFO")
            return False
        else:
            self.log("boto3 is installed", "OK")
        
        # Test 2: Credentials
        if self.access_key and self.secret_key:
            self.log(f"Using provided credentials (Access Key: {self.access_key[:8]}...)", "INFO")
        else:
            self.log("Using default AWS credentials (env vars or ~/.aws/credentials)", "INFO")
        
        self.log(f"Region: {self.region}", "INFO")
        self.log("", "INFO")
        
        # Test 3: Create client
        try:
            if self.access_key and self.secret_key:
                s3_client = boto3.client(
                    's3',
                    aws_access_key_id=self.access_key,
                    aws_secret_access_key=self.secret_key,
                    region_name=self.region
                )
            else:
                s3_client = boto3.client('s3', region_name=self.region)
            
            self.log("S3 client created successfully", "OK")
        except NoCredentialsError:
            self.log("No AWS credentials found", "ERROR")
            self.log("Configure credentials using one of:", "INFO")
            self.log("  1. Environment variables: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY", "INFO")
            self.log("  2. File: ~/.aws/credentials", "INFO")
            self.log("  3. Enter credentials manually in the UI", "INFO")
            return False
        except PartialCredentialsError:
            self.log("Incomplete credentials provided", "ERROR")
            self.log("Both Access Key and Secret Key are required", "INFO")
            return False
        except Exception as e:
            self.log(f"Failed to create S3 client: {str(e)}", "ERROR")
            return False
        
        # Test 4: Get caller identity (verify credentials)
        try:
            sts = boto3.client('sts',
                              aws_access_key_id=self.access_key,
                              aws_secret_access_key=self.secret_key,
                              region_name=self.region) if self.access_key else boto3.client('sts')
            identity = sts.get_caller_identity()
            self.log(f"AWS Account: {identity.get('Account', 'Unknown')}", "OK")
            self.log(f"User ARN: {identity.get('Arn', 'Unknown')}", "INFO")
            self.log("", "INFO")
        except Exception as e:
            self.log(f"Cannot verify identity: {str(e)}", "WARN")
            self.log("This might indicate invalid credentials", "WARN")
            self.log("", "INFO")
        
        # Test 5: List buckets (main test)
        try:
            response = s3_client.list_buckets()
            buckets = response.get('Buckets', [])
            
            if not buckets:
                self.log("No S3 buckets found in this account", "WARN")
                self.log("", "INFO")
                self.log("Possible reasons:", "INFO")
                self.log("  1. Your AWS account has no S3 buckets", "INFO")
                self.log("  2. The IAM user/role lacks 's3:ListAllMyBuckets' permission", "INFO")
                self.log("  3. Buckets exist in a different region", "INFO")
                self.log("", "INFO")
                self.log("To create a bucket:", "INFO")
                self.log("  - AWS Console: https://s3.console.aws.amazon.com/", "INFO")
                self.log("  - AWS CLI: aws s3 mb s3://my-bucket-name", "INFO")
                return True  # Connection OK, just no buckets
            else:
                self.log(f"Found {len(buckets)} bucket(s):", "OK")
                for bucket in buckets[:10]:  # Show first 10
                    name = bucket.get('Name', 'Unknown')
                    created = bucket.get('CreationDate', '')
                    self.log(f"  • {name} (created: {created})", "INFO")
                if len(buckets) > 10:
                    self.log(f"  ... and {len(buckets) - 10} more", "INFO")
                self.log("", "INFO")
                self.log("Connection successful!", "OK")
                return True
                
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            
            self.log(f"S3 API Error: {error_code}", "ERROR")
            self.log(f"Message: {error_msg}", "ERROR")
            self.log("", "INFO")
            
            if error_code == 'AccessDenied':
                self.log("Access Denied - Your IAM user/role needs these permissions:", "INFO")
                self.log("  • s3:ListAllMyBuckets", "INFO")
                self.log("  • s3:GetBucketLocation", "INFO")
                self.log("  • s3:ListBucket", "INFO")
                self.log("  • s3:GetObject", "INFO")
            elif error_code == 'InvalidAccessKeyId':
                self.log("Invalid Access Key ID - Check your credentials", "INFO")
            elif error_code == 'SignatureDoesNotMatch':
                self.log("Invalid Secret Key - Check your credentials", "INFO")
            
            return False
            
        except Exception as e:
            self.log(f"Unexpected error: {str(e)}", "ERROR")
            return False
    
    def get_results(self) -> str:
        return "\n".join(self.results)


class AWSDiagnosticUI:
    """UI for AWS connection diagnostics."""
    
    def __init__(self, parent: tk.Tk):
        self.window = tk.Toplevel(parent)
        self.window.title("AWS S3 Connection Diagnostic")
        self.window.geometry("700x600")
        
        if not BOTO3_AVAILABLE:
            tk.Label(
                self.window,
                text="❌ boto3 not installed\n\nInstall with:\npip install boto3",
                fg="red",
                font=("Arial", 12),
                justify="center"
            ).pack(pady=30)
            tk.Button(self.window, text="Close", command=self.window.destroy).pack()
            return
        
        self._build_ui()
    
    def _build_ui(self):
        # Title
        tk.Label(
            self.window,
            text="AWS S3 Connection Diagnostic",
            font=("Arial", 14, "bold")
        ).pack(pady=10)
        
        # Credentials frame
        cred_frame = tk.LabelFrame(self.window, text="AWS Credentials (optional)", padx=10, pady=10)
        cred_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(cred_frame, text="Access Key:").grid(row=0, column=0, sticky="w", padx=5)
        self.access_entry = tk.Entry(cred_frame, width=40, show="*")
        self.access_entry.grid(row=0, column=1, padx=5, pady=2)
        
        tk.Label(cred_frame, text="Secret Key:").grid(row=1, column=0, sticky="w", padx=5)
        self.secret_entry = tk.Entry(cred_frame, width=40, show="*")
        self.secret_entry.grid(row=1, column=1, padx=5, pady=2)
        
        tk.Label(cred_frame, text="Region:").grid(row=2, column=0, sticky="w", padx=5)
        self.region_entry = tk.Entry(cred_frame, width=40)
        self.region_entry.insert(0, "us-east-1")
        self.region_entry.grid(row=2, column=1, padx=5, pady=2)
        
        tk.Label(
            cred_frame,
            text="Leave empty to use default credentials (env vars or ~/.aws/credentials)",
            font=("Arial", 8),
            fg="gray"
        ).grid(row=3, column=0, columnspan=2, pady=5)
        
        # Test button
        tk.Button(
            self.window,
            text="Run Diagnostic",
            command=self._run_test,
            font=("Arial", 11, "bold"),
            bg="#4CAF50",
            fg="white",
            padx=20,
            pady=5
        ).pack(pady=10)
        
        # Results frame
        result_frame = tk.LabelFrame(self.window, text="Diagnostic Results", padx=10, pady=10)
        result_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        self.result_text = scrolledtext.ScrolledText(
            result_frame,
            wrap=tk.WORD,
            width=80,
            height=20,
            font=("Courier", 9)
        )
        self.result_text.pack(fill=tk.BOTH, expand=True)
        
        # Close button
        tk.Button(
            self.window,
            text="Close",
            command=self.window.destroy,
            width=15
        ).pack(pady=10)
    
    def _run_test(self):
        access_key = self.access_entry.get().strip() or None
        secret_key = self.secret_entry.get().strip() or None
        region = self.region_entry.get().strip() or "us-east-1"
        
        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, "Running diagnostic...\n\n")
        self.window.update()
        
        tester = AWSConnectionTester(access_key, secret_key, region)
        success = tester.test_connection()
        
        self.result_text.delete(1.0, tk.END)
        self.result_text.insert(tk.END, tester.get_results())
        
        if success:
            self.result_text.insert(tk.END, "\n\n" + "="*50)
            self.result_text.insert(tk.END, "\n✓ Connection test completed successfully")
        else:
            self.result_text.insert(tk.END, "\n\n" + "="*50)
            self.result_text.insert(tk.END, "\n✗ Connection test failed - see details above")


def open_aws_diagnostic(parent: tk.Tk):
    """Open AWS diagnostic tool."""
    AWSDiagnosticUI(parent)
