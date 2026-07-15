"""
AWS S3 import module for scenario data.
Requires: boto3, pandas (optional for data transformation)
Install with: pip install boto3 pandas
"""
from __future__ import annotations

import io
import csv
import json
import os
from typing import Optional, List, Dict, Any
from tkinter import messagebox
import tkinter as tk
from tkinter import ttk

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

from app.logging_setup import setup_logging
from app.save_paths import ensure_devices_dir

logger = setup_logging("io.aws_import")


class AWSS3Importer:
    """Handle AWS S3 connections and data import."""
    
    def __init__(self, aws_access_key: Optional[str] = None,
                 aws_secret_key: Optional[str] = None,
                 region_name: str = 'eu-central-1'):
        """
        Initialize AWS S3 client.
        If credentials are None, will use default AWS credentials (env vars, ~/.aws/credentials, IAM role).
        """
        if not BOTO3_AVAILABLE:
            raise ImportError("boto3 not installed. Run: pip install boto3")
        
        try:
            if aws_access_key and aws_secret_key:
                self.s3_client = boto3.client(
                    's3',
                    aws_access_key_id=aws_access_key,
                    aws_secret_access_key=aws_secret_key,
                    region_name=region_name
                )
            else:
                # Use default credentials
                self.s3_client = boto3.client('s3', region_name=region_name)
            
            logger.info("AWS S3 client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize AWS S3 client: {e}")
            raise
    
    def list_buckets(self) -> List[str]:
        """List all available S3 buckets."""
        try:
            response = self.s3_client.list_buckets()
            buckets = [bucket['Name'] for bucket in response.get('Buckets', [])]
            logger.info(f"Found {len(buckets)} S3 buckets")
            return buckets
        except NoCredentialsError as e:
            logger.error("No AWS credentials found")
            raise Exception("No AWS credentials configured. Please set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY environment variables or enter credentials manually.")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"AWS S3 Error ({error_code}): {error_msg}")
            
            if error_code == 'AccessDenied':
                raise Exception(f"Access Denied: Your IAM user/role needs 's3:ListAllMyBuckets' permission.\n\nError: {error_msg}")
            elif error_code == 'InvalidAccessKeyId':
                raise Exception(f"Invalid Access Key ID. Please check your credentials.\n\nError: {error_msg}")
            elif error_code == 'SignatureDoesNotMatch':
                raise Exception(f"Invalid Secret Key. Please check your credentials.\n\nError: {error_msg}")
            else:
                raise Exception(f"AWS Error ({error_code}): {error_msg}")
        except Exception as e:
            logger.error(f"Error listing buckets: {e}")
            raise Exception(f"Failed to list S3 buckets: {str(e)}")
    
    def list_objects(self, bucket_name: str, prefix: str = '') -> List[str]:
        """List **all** objects in a specific bucket with optional prefix (handles pagination)."""
        try:
            paginator = self.s3_client.get_paginator('list_objects_v2')
            page_iterator = paginator.paginate(Bucket=bucket_name, Prefix=prefix)

            objects: List[str] = []
            for page in page_iterator:
                contents = page.get('Contents', [])
                if contents:
                    objects.extend(obj['Key'] for obj in contents)

            logger.info(
                f"Found {len(objects)} objects in bucket '{bucket_name}'" +
                (f" with prefix '{prefix}'" if prefix else "")
            )
            return objects
        except Exception as e:
            logger.error(f"Error listing objects in bucket '{bucket_name}': {e}")
            return []
    
    def download_csv_file(self, bucket_name: str, key: str) -> Optional[str]:
        """Download CSV file from S3 and return content as string."""
        try:
            response = self.s3_client.get_object(Bucket=bucket_name, Key=key)
            content = response['Body'].read().decode('utf-8')
            return content
        except Exception as e:
            logger.error(f"Error downloading '{key}' from '{bucket_name}': {e}")
            return None
    
    def download_json_file(self, bucket_name: str, key: str) -> Optional[Dict[str, Any]]:
        """Download JSON file from S3 and return as dictionary."""
        try:
            response = self.s3_client.get_object(Bucket=bucket_name, Key=key)
            content = response['Body'].read().decode('utf-8')
            data = json.loads(content)
            logger.info(f"Downloaded JSON file '{key}' from bucket '{bucket_name}'")
            return data
        except Exception as e:
            logger.error(f"Error downloading JSON '{key}' from '{bucket_name}': {e}")
            return None
    
    def save_to_local_csv(self, content: str, local_path: str) -> bool:
        """Save downloaded content to local CSV file."""
        try:
            with open(local_path, 'w', encoding='utf-8') as f:
                f.write(content)
            logger.info(f"Saved content to '{local_path}'")
            return True
        except Exception as e:
            logger.error(f"Error saving to '{local_path}': {e}")
            return False


def transform_grafana_to_scenario(grafana_data: Dict[str, Any]) -> str:
    """
    Transform Grafana/TwinMaker time-series data to scenario CSV format.
    This is a template - customize based on your actual data structure.
    """
    # TODO: Customize based on your Grafana data structure
    csv_lines = []
    csv_lines.append("Positions")
    
    # Example: if grafana_data contains sensor positions
    if 'sensors' in grafana_data:
        for sensor in grafana_data['sensors']:
            # Assuming sensor has: name, x, y, type, etc.
            pass
    
    csv_lines.append("")
    csv_lines.append("Walls")
    # Add wall data transformation...
    
    csv_lines.append("")
    csv_lines.append("Sensors")
    # Add sensor data transformation...
    
    csv_lines.append("")
    csv_lines.append("Devices")
    # Add device data transformation...
    
    csv_lines.append("")
    csv_lines.append("Doors")
    # Add door data transformation...
    
    return "\n".join(csv_lines)


class AWSS3ImportUI:
    """UI for AWS S3 import."""
    
    def __init__(self, parent: tk.Tk):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("Import from AWS S3")
        self.window.geometry("800x650")
        self.window.resizable(True, True)
        
        self.importer: Optional[AWSS3Importer] = None
        self.selected_bucket = None
        self.selected_file = None
        
        self._build_ui()
    
    def _build_ui(self):
        # Check boto3 availability
        if not BOTO3_AVAILABLE:
            tk.Label(
                self.window,
                text="❌ boto3 not installed\nRun: pip install boto3",
                fg="red",
                font=("Arial", 12)
            ).pack(pady=20)
            tk.Button(self.window, text="Close", command=self.window.destroy).pack()
            return
        
        # Credentials Frame
        cred_frame = tk.LabelFrame(self.window, text="AWS Credentials", padx=10, pady=10)
        cred_frame.pack(fill=tk.X, padx=10, pady=10)
        
        tk.Label(cred_frame, text="Access Key (leave empty for default):").grid(row=0, column=0, sticky="w")
        self.access_key_entry = tk.Entry(cred_frame, width=40, show="*")
        self.access_key_entry.grid(row=0, column=1, padx=5, pady=2)
        
        tk.Label(cred_frame, text="Secret Key (leave empty for default):").grid(row=1, column=0, sticky="w")
        self.secret_key_entry = tk.Entry(cred_frame, width=40, show="*")
        self.secret_key_entry.grid(row=1, column=1, padx=5, pady=2)
        
        tk.Label(cred_frame, text="Region:").grid(row=2, column=0, sticky="w")
        self.region_entry = tk.Entry(cred_frame, width=40)
        self.region_entry.insert(0, "us-east-1")
        self.region_entry.grid(row=2, column=1, padx=5, pady=2)
        
        tk.Button(cred_frame, text="Connect", command=self._connect_to_aws).grid(row=3, column=1, pady=5, sticky="e")
        
        # Buckets Frame
        bucket_frame = tk.LabelFrame(self.window, text="S3 Buckets", padx=10, pady=10)
        bucket_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        tk.Label(bucket_frame, text="Select Bucket (or enter manually below):").pack(anchor="w")
        self.bucket_listbox = tk.Listbox(bucket_frame, height=5)
        self.bucket_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        self.bucket_listbox.bind('<<ListboxSelect>>', self._on_bucket_select)
        
        # Manual bucket entry
        manual_frame = tk.Frame(bucket_frame)
        manual_frame.pack(fill=tk.X, pady=5)
        tk.Label(manual_frame, text="Or enter bucket name:").pack(side="left", padx=5)
        self.manual_bucket_entry = tk.Entry(manual_frame, width=30)
        self.manual_bucket_entry.pack(side="left", padx=5)
        tk.Button(manual_frame, text="Use This Bucket", command=self._use_manual_bucket).pack(side="left", padx=5)
        
        # Files Frame
        files_frame = tk.LabelFrame(self.window, text="Files in Bucket", padx=10, pady=10)
        files_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        tk.Label(files_frame, text="Select File:").pack(anchor="w")
        self.files_listbox = tk.Listbox(files_frame, height=5)
        self.files_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        self.files_listbox.bind('<<ListboxSelect>>', self._on_file_select)
        
        # Action Buttons
        btn_frame = tk.Frame(self.window)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        tk.Button(btn_frame, text="Import as Scenario", command=self._import_scenario, state="disabled").pack(side="left", padx=5)
        self.import_btn = btn_frame.winfo_children()[0]
        
        tk.Button(btn_frame, text="Download to File", command=self._download_to_file, state="disabled").pack(side="left", padx=5)
        self.download_btn = btn_frame.winfo_children()[1]
        
        tk.Button(btn_frame, text="Close", command=self.window.destroy).pack(side="right", padx=5)
        
        # Status
        self.status_label = tk.Label(self.window, text="Status: Not connected", fg="gray")
        self.status_label.pack(pady=5)
    
    def _connect_to_aws(self):
        access_key = self.access_key_entry.get().strip() or None
        secret_key = self.secret_key_entry.get().strip() or None
        region = self.region_entry.get().strip() or "us-east-1"
        
        try:
            self.importer = AWSS3Importer(access_key, secret_key, region)
            self.status_label.config(text="Status: Connected ✓", fg="green")
            self._load_buckets()
        except Exception as e:
            messagebox.showerror("Connection Error", f"Failed to connect to AWS:\n{str(e)}")
            self.status_label.config(text="Status: Connection failed ✗", fg="red")
    
    def _load_buckets(self):
        if not self.importer:
            return
        
        try:
            buckets = self.importer.list_buckets()
            self.bucket_listbox.delete(0, tk.END)
            for bucket in buckets:
                self.bucket_listbox.insert(tk.END, bucket)
            
            if not buckets:
                messagebox.showinfo(
                    "No Buckets", 
                    "No S3 buckets found.\n\nIf you know the bucket name, enter it manually below."
                )
        except Exception as e:
            error_msg = str(e)
            if "AccessDenied" in error_msg or "not authorized" in error_msg:
                messagebox.showinfo(
                    "Permission Required",
                    "Your IAM user lacks 's3:ListAllMyBuckets' permission.\n\n"
                    "You can still use S3 by entering the bucket name manually below."
                )
            else:
                messagebox.showerror("Error", f"Failed to list buckets:\n{error_msg}")
    
    def _on_bucket_select(self, event):
        selection = self.bucket_listbox.curselection()
        if not selection:
            return
        
        self.selected_bucket = self.bucket_listbox.get(selection[0])
        self.manual_bucket_entry.delete(0, tk.END)
        self.manual_bucket_entry.insert(0, self.selected_bucket)
        self._load_files()
    
    def _use_manual_bucket(self):
        bucket = self.manual_bucket_entry.get().strip()
        if not bucket:
            messagebox.showwarning("Bucket Required", "Please enter a bucket name.")
            return
        
        self.selected_bucket = bucket
        self.status_label.config(text=f"Using bucket: {bucket}", fg="blue")
        self._load_files()
    
    def _load_files(self):
        if not self.importer or not self.selected_bucket:
            return
        
        self.status_label.config(text=f"Loading files from '{self.selected_bucket}'...", fg="blue")
        self.window.update()
        
        try:
            files = self.importer.list_objects(self.selected_bucket)
            self.files_listbox.delete(0, tk.END)
            
            csv_json = [f for f in files if f.endswith(('.csv', '.json'))]
            for file in csv_json:
                self.files_listbox.insert(tk.END, file)
            
            if not csv_json:
                self.status_label.config(text=f"No CSV/JSON files in '{self.selected_bucket}'", fg="orange")
                messagebox.showinfo("No Files", f"No CSV/JSON files found in bucket '{self.selected_bucket}'.")
            else:
                self.status_label.config(text=f"Loaded {len(csv_json)} file(s) from '{self.selected_bucket}'", fg="green")
        except Exception as e:
            self.status_label.config(text="Failed to list files", fg="red")
            msg = str(e)
            if "NoSuchBucket" in msg:
                messagebox.showerror("Bucket Not Found", f"Bucket '{self.selected_bucket}' does not exist or is not accessible.")
            elif "AccessDenied" in msg:
                messagebox.showerror("Access Denied", f"Missing permission s3:ListBucket on '{self.selected_bucket}'.")
            else:
                messagebox.showerror("Error", f"Failed to list files:\n{msg}")
    
    def _on_file_select(self, event):
        selection = self.files_listbox.curselection()
        if not selection:
            return
        
        self.selected_file = self.files_listbox.get(selection[0])
        self.import_btn.config(state="normal")
        self.download_btn.config(state="normal")
    
    def _import_scenario(self):
        if not self.importer or not self.selected_bucket or not self.selected_file:
            messagebox.showwarning("Selection Required", "Please select a bucket and file.")
            return
        
        self.status_label.config(text=f"Downloading '{self.selected_file}'...", fg="blue")
        self.window.update()
        
        content = self.importer.download_csv_file(self.selected_bucket, self.selected_file)
        if content:
            # Ask for file type/name to determine the save path
            file_type = messagebox.askquestion(
                "File Type",
                "Is this a TEMPERATURE sensor file (like t1, t2, etc.)?\n\n"
                "Click YES to save as dht_<name>.csv\n"
                "Click NO to save as saved.csv (for scenarios)"
            )
            
            if file_type == 'yes':
                # Ask for the sensor name
                from tkinter import simpledialog
                sensor_name = simpledialog.askstring(
                    "Sensor Name",
                    "Enter sensor name (e.g., t1, t2, bedroom_temp):"
                )
                
                if sensor_name:
                    sensor_name = sensor_name.strip()
                    local_path = os.path.join(str(ensure_devices_dir()), f"dht_{sensor_name}.csv")
                else:
                    messagebox.showwarning("Cancelled", "Import cancelled.")
                    self.status_label.config(text="Import cancelled", fg="orange")
                    return
            else:
                local_path = "saved.csv"
            
            if self.importer.save_to_local_csv(content, local_path):
                messagebox.showinfo("Success", f"Imported '{self.selected_file}' to {local_path}\n\nUse File → Load default to open it.")
                self.status_label.config(text="Import successful ✓", fg="green")
                self.window.destroy()
            else:
                messagebox.showerror("Error", "Failed to save imported data.")
                self.status_label.config(text="Import failed ✗", fg="red")
        else:
            messagebox.showerror("Error", "Failed to download file from S3.")
            self.status_label.config(text="Download failed ✗", fg="red")
    
    def _download_to_file(self):
        from app.io.safe_dialog import ask_save_file

        if not self.importer or not self.selected_bucket or not self.selected_file:
            messagebox.showwarning("Selection Required", "Please select a bucket and file.")
            return

        local_path = ask_save_file(
            title="Save As",
            defaultextension=".csv",
            initialfile=self.selected_file,
            filetypes=[("CSV files", "*.csv"), ("JSON files", "*.json"), ("All files", "*.*")],
        )
        
        if not local_path:
            return
        
        self.status_label.config(text=f"Downloading '{self.selected_file}'...", fg="blue")
        self.window.update()
        
        content = self.importer.download_csv_file(self.selected_bucket, self.selected_file)
        if content and self.importer.save_to_local_csv(content, local_path):
            messagebox.showinfo("Success", f"Downloaded to:\n{local_path}")
            self.status_label.config(text="Download successful ✓", fg="green")
        else:
            messagebox.showerror("Error", "Failed to download file.")
            self.status_label.config(text="Download failed ✗", fg="red")


def open_aws_import_ui(parent: tk.Tk):
    """Open the AWS S3 import dialog."""
    AWSS3ImportUI(parent)
