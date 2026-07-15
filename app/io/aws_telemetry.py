"""
AWS S3 telemetry data importer for DHT22 and Smart Meter logs.
Imports time-series JSON data from S3 and applies it to bound sensors/devices.
"""
from __future__ import annotations

import json
import csv
import io
import os
from typing import Optional, List, Dict, Any
from datetime import datetime
from tkinter import messagebox
import tkinter as tk
from tkinter import ttk, scrolledtext

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False

from app.logging_setup import setup_logging
from app.save_paths import ensure_devices_dir

logger = setup_logging("io.aws_telemetry")


def _sanitize_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in (name or "").strip())


class AWSTelemetryImporter:
    """Import telemetry data (DHT22, Smart Meter) from AWS S3."""
    
    def __init__(self, aws_access_key: Optional[str] = None, 
                 aws_secret_key: Optional[str] = None,
                 region_name: str = 'eu-central-1'):
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
                self.s3_client = boto3.client('s3', region_name=region_name)
            
            logger.info("AWS S3 client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize AWS S3: {e}")
            raise
    
    def list_buckets(self) -> List[str]:
        try:
            response = self.s3_client.list_buckets()
            return [b['Name'] for b in response.get('Buckets', [])]
        except Exception as e:
            logger.error(f"Error listing buckets: {e}")
            return []
    
    def list_objects(self, bucket: str, prefix: str = '') -> List[Dict[str, Any]]:
        """List objects with metadata (Key, Size, LastModified)."""
        try:
            response = self.s3_client.list_objects_v2(Bucket=bucket, Prefix=prefix)
            return response.get('Contents', [])
        except Exception as e:
            logger.error(f"Error listing objects: {e}")
            return []
    
    def download_file(self, bucket: str, key: str) -> Optional[str]:
        """Download file as string."""
        try:
            obj = self.s3_client.get_object(Bucket=bucket, Key=key)
            content = obj['Body'].read().decode('utf-8')
            logger.info(f"Downloaded {key} from {bucket} ({len(content)} bytes)")
            return content
        except Exception as e:
            logger.error(f"Error downloading {key}: {e}")
            return None
    
    def detect_data_type(self, content: str) -> Optional[str]:
        """Detect if content is DHT or SmartMeter JSON by examining first valid JSON line."""
        for line in content.split('\n'):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                # Check for SmartMeter fields (check first for priority)
                if 'apower' in data or 'power_w' in data or 'power' in data:
                    logger.info(f"Detected SmartMeter data: {list(data.keys())}")
                    return 'smartmeter'
                if 'voltage' in data or 'voltage_v' in data:
                    logger.info(f"Detected SmartMeter data (voltage): {list(data.keys())}")
                    return 'smartmeter'
                if 'current' in data or 'current_a' in data:
                    logger.info(f"Detected SmartMeter data (current): {list(data.keys())}")
                    return 'smartmeter'
                if 'ts' in data and ('energy_total' in data or 'energy' in data):
                    logger.info(f"Detected SmartMeter data (ts+energy): {list(data.keys())}")
                    return 'smartmeter'
                # Check for DHT fields
                if 'temperature_c' in data or 'humidity_rh' in data or 'label' in data:
                    logger.info(f"Detected DHT data: {list(data.keys())}")
                    return 'dht'
            except Exception as e:
                logger.warning(f"Failed to parse JSON line: {e}")
                continue
        logger.warning(f"Could not detect data type from content (first 200 chars): {content[:200]}")
        return None
    
    def parse_dht_data(self, content: str) -> List[Dict[str, Any]]:
        """
        Parse DHT22 data from JSON lines format.
        Expected: {"timestamp_iso": 1765481925, "label": "t1", "gpio": 4, "temperature_c": 16.4, "humidity_rh": 74.6}
        """
        records = []
        for line in content.strip().split('\n'):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                records.append({
                    'timestamp': data.get('timestamp_iso'),
                    'label': data.get('label'),
                    'gpio': data.get('gpio'),
                    'temperature': data.get('temperature_c'),
                    'humidity': data.get('humidity_rh')
                })
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON line: {line[:50]}... - {e}")
        return records
    
    def parse_smartmeter_data(self, content: str) -> List[Dict[str, Any]]:
        """
        Parse Smart Meter data from JSON lines.
        Expected: {"ts":1765405428638,"apower":37.5,"voltage":229.7,"current":0.291,"energy_total":134.083}
        """
        records = []
        for line in content.strip().split('\n'):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                records.append({
                    'timestamp': data.get('ts'),
                    'power': data.get('apower'),
                    'voltage': data.get('voltage'),
                    'current': data.get('current'),
                    'energy': data.get('energy_total')
                })
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON line: {line[:50]}... - {e}")
        return records
    
    def save_dht_to_csv(self, records: List[Dict], filepath: str) -> bool:
        """Save DHT records to CSV in dhtlogger format."""
        try:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp_iso', 'label', 'gpio', 'temp_C', 'hum_%'])
                for r in records:
                    writer.writerow([
                        r.get('timestamp', ''),
                        r.get('label', ''),
                        r.get('gpio', ''),
                        r.get('temperature', ''),
                        r.get('humidity', '')
                    ])
            logger.info(f"Saved {len(records)} DHT records to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to save DHT CSV: {e}")
            return False
    
    def save_smartmeter_to_csv(self, records: List[Dict], filepath: str, device_name: str = "device") -> bool:
        """Save Smart Meter records to CSV in smartmeter format."""
        try:
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['timestamp_iso', 'device', 'device_id', 'ip', 'power_W', 'voltage_V', 'current_A'])
                for r in records:
                    writer.writerow([
                        r.get('timestamp', ''),
                        device_name,
                        device_name,
                        'imported',
                        r.get('power', ''),
                        r.get('voltage', ''),
                        r.get('current', '')
                    ])
            logger.info(f"Saved {len(records)} Smart Meter records to {filepath}")
            return True
        except Exception as e:
            logger.error(f"Failed to save Smart Meter CSV: {e}")
            return False


class AWSTelemetryImportUI:
    """UI for importing telemetry data from AWS S3."""
    
    def __init__(self, parent: tk.Tk):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("Import Telemetry from AWS S3")
        self.window.geometry("750x650")
        
        self.importer: Optional[AWSTelemetryImporter] = None
        self.selected_bucket = None
        self.selected_files = []
        
        self._build_ui()
    
    def _build_ui(self):
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
        
        # Credentials
        cred_frame = tk.LabelFrame(self.window, text="AWS Credentials", padx=10, pady=10)
        cred_frame.pack(fill=tk.X, padx=10, pady=10)
        
        tk.Label(cred_frame, text="Access Key (optional):").grid(row=0, column=0, sticky="w", padx=5)
        self.access_key_entry = tk.Entry(cred_frame, width=35, show="*")
        self.access_key_entry.grid(row=0, column=1, padx=5, pady=2)
        
        tk.Label(cred_frame, text="Secret Key (optional):").grid(row=1, column=0, sticky="w", padx=5)
        self.secret_key_entry = tk.Entry(cred_frame, width=35, show="*")
        self.secret_key_entry.grid(row=1, column=1, padx=5, pady=2)
        
        tk.Label(cred_frame, text="Region:").grid(row=2, column=0, sticky="w", padx=5)
        self.region_entry = tk.Entry(cred_frame, width=35)
        self.region_entry.insert(0, "us-east-1")
        self.region_entry.grid(row=2, column=1, padx=5, pady=2)
        
        tk.Button(cred_frame, text="Connect", command=self._connect).grid(row=3, column=1, pady=5, sticky="e")
        
        # Buckets
        bucket_frame = tk.LabelFrame(self.window, text="S3 Buckets", padx=10, pady=10)
        bucket_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        tk.Label(bucket_frame, text="Select bucket (or enter manually):").pack(anchor="w")
        self.bucket_listbox = tk.Listbox(bucket_frame, height=4)
        self.bucket_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        self.bucket_listbox.bind('<<ListboxSelect>>', self._on_bucket_select)

        manual_bucket = tk.Frame(bucket_frame)
        manual_bucket.pack(fill=tk.X, pady=4)
        tk.Label(manual_bucket, text="Bucket name:").pack(side="left", padx=5)
        self.manual_bucket_entry = tk.Entry(manual_bucket, width=30)
        self.manual_bucket_entry.pack(side="left", padx=5)
        tk.Button(manual_bucket, text="Use", command=self._use_manual_bucket).pack(side="left", padx=5)
        
        # Files
        files_frame = tk.LabelFrame(self.window, text="Files (select DHT or Smart Meter logs)", padx=10, pady=10)
        files_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        tk.Label(files_frame, text="Select files:").pack(anchor="w")
        self.files_listbox = tk.Listbox(files_frame, height=6, selectmode=tk.MULTIPLE)
        self.files_listbox.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # Data Type
        type_frame = tk.Frame(self.window)
        type_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(type_frame, text="Data Type:").pack(side="left", padx=5)
        self.data_type_var = tk.StringVar(value="smartmeter")
        tk.Radiobutton(type_frame, text="DHT22 (Temperature/Humidity)", variable=self.data_type_var, value="dht", command=self._on_data_type_change).pack(side="left", padx=10)
        tk.Radiobutton(type_frame, text="Smart Meter (Power)", variable=self.data_type_var, value="smartmeter", command=self._on_data_type_change).pack(side="left", padx=10)
        tk.Radiobutton(type_frame, text="All JSON files", variable=self.data_type_var, value="all", command=self._on_data_type_change).pack(side="left", padx=10)
        
        # Actions
        btn_frame = tk.Frame(self.window)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        self.import_btn = tk.Button(btn_frame, text="Import to devices/", command=self._import_data, state="disabled")
        self.import_btn.pack(side="left", padx=5)
        
        self.import_all_btn = tk.Button(btn_frame, text="Import ALL to one CSV", command=self._import_all, state="disabled")
        self.import_all_btn.pack(side="left", padx=5)

        self.combine_json_btn = tk.Button(btn_frame, text="Combine ALL to one JSON", command=self._combine_all_json, state="disabled")
        self.combine_json_btn.pack(side="left", padx=5)
        
        self.preview_btn = tk.Button(btn_frame, text="Preview Data", command=self._preview_data, state="disabled")
        self.preview_btn.pack(side="left", padx=5)
        
        tk.Button(btn_frame, text="Close", command=self.window.destroy).pack(side="right", padx=5)
        
        # Status
        self.status_label = tk.Label(self.window, text="Status: Not connected", fg="gray", font=("Arial", 9))
        self.status_label.pack(pady=5)
    
    def _connect(self):
        access_key = self.access_key_entry.get().strip() or None
        secret_key = self.secret_key_entry.get().strip() or None
        region = self.region_entry.get().strip() or "us-east-1"
        
        try:
            self.importer = AWSTelemetryImporter(access_key, secret_key, region)
            self.status_label.config(text="✓ Connected to AWS", fg="green")
            self._load_buckets()
        except Exception as e:
            messagebox.showerror("Connection Error", f"Failed to connect:\n{str(e)}")
            self.status_label.config(text="✗ Connection failed", fg="red")
    
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
                    "No S3 buckets found. If you know the bucket name, enter it manually."
                )
        except Exception as e:
            msg = str(e)
            if "AccessDenied" in msg or "not authorized" in msg:
                messagebox.showinfo(
                    "Permission Required",
                    "Your IAM user lacks 's3:ListAllMyBuckets'. Enter the bucket name manually."
                )
            else:
                messagebox.showerror("Error", f"Failed to list buckets:\n{msg}")
    
    def _on_bucket_select(self, event):
        sel = self.bucket_listbox.curselection()
        if not sel:
            return
        
        self.selected_bucket = self.bucket_listbox.get(sel[0])
        self.manual_bucket_entry.delete(0, tk.END)
        self.manual_bucket_entry.insert(0, self.selected_bucket)
        self._load_files()

    def _use_manual_bucket(self):
        bucket = (self.manual_bucket_entry.get() or "").strip()
        if not bucket:
            messagebox.showwarning("Bucket Required", "Please enter a bucket name.")
            return
        self.selected_bucket = bucket
        self.status_label.config(text=f"Using bucket: {bucket}", fg="blue")
        self._load_files()
    
    def _on_data_type_change(self):
        """Reload files when data type selection changes."""
        self._load_files()
    
    def _load_files(self):
        if not self.importer or not self.selected_bucket:
            return
        
        self.status_label.config(text=f"Loading files from '{self.selected_bucket}'...", fg="blue")
        self.window.update()
        try:
            objects = self.importer.list_objects(self.selected_bucket)
            logger.info(f"Found {len(objects)} total objects in bucket")
            self.files_listbox.delete(0, tk.END)
            
            data_type = self.data_type_var.get()
            filtered = []
            json_count = 0
            
            for obj in objects:
                key = obj.get('Key', '')
                # Only check files with JSON-like extensions
                if not key.endswith(('.json', '.csv', '.log', '.txt')):
                    logger.debug(f"Skipping {key} (unsupported extension)")
                    continue
                
                if key.endswith('.json'):
                    json_count += 1
                
                logger.info(f"Checking file: {key}")
                # Download and detect data type
                content = self.importer.download_file(self.selected_bucket, key)
                if not content:
                    logger.warning(f"Empty or failed download: {key}")
                    continue
                
                logger.info(f"Downloaded {len(content)} bytes from {key}")
                detected_type = self.importer.detect_data_type(content)
                logger.info(f"File {key} detected as: {detected_type} (looking for: {data_type})")
                
                # Only include files matching selected data type (or all if data_type == 'all')
                if data_type == 'all' or detected_type == data_type:
                    filtered.append(key)
                    display_text = f"{key} [{detected_type or 'unknown'}]" if data_type == 'all' else key
                    self.files_listbox.insert(tk.END, display_text)
            
            logger.info(f"Found {json_count} JSON files, {len(filtered)} matched {data_type} type")
            
            if not filtered:
                msg = "No DHT22 files found" if data_type == "dht" else ("No Smart Meter files found" if data_type == "smartmeter" else "No JSON files found")
                self.status_label.config(text=msg, fg="orange")
                messagebox.showinfo("No Files", f"{msg} in bucket '{self.selected_bucket}'.")
            else:
                self.status_label.config(text=f"✓ Loaded {len(filtered)} file(s)", fg="green")
                self.import_btn.config(state="normal")
                self.preview_btn.config(state="normal")
                self.combine_json_btn.config(state="normal")
                self.import_all_btn.config(state="normal")
        except Exception as e:
            self.status_label.config(text="✗ Failed to list files", fg="red")
            msg = str(e)
            if "NoSuchBucket" in msg:
                messagebox.showerror("Bucket Not Found", f"Bucket '{self.selected_bucket}' not found or not accessible.")
            elif "AccessDenied" in msg:
                messagebox.showerror("Access Denied", f"Missing permission s3:ListBucket on '{self.selected_bucket}'.")
            else:
                messagebox.showerror("Error", f"Failed to list files:\n{msg}")
    
    def _import_data(self):
        sel = self.files_listbox.curselection()
        if not sel:
            messagebox.showwarning("No Selection", "Please select at least one file.")
            return
        
        files = [self.files_listbox.get(i) for i in sel]
        data_type = self.data_type_var.get()
        
        # Ask for output base name
        default_name = "dht_import" if data_type == "dht" else "smartmeter_import"
        output_name = tk.simpledialog.askstring(
            "Output CSV name",
            "Enter the name for the new CSV (without .csv):",
            parent=self.window,
            initialvalue=default_name
        )
        if not output_name:
            return
        
        original_name = output_name
        output_name = _sanitize_name(output_name)
        
        # Add prefix if not already present
        if data_type == "dht" and not output_name.startswith("dht_"):
            output_name = f"dht_{output_name}"
        elif data_type == "smartmeter" and not output_name.startswith("smartmeter_"):
            output_name = f"smartmeter_{output_name}"
        
        print(f"DEBUG: original='{original_name}', sanitized='{_sanitize_name(original_name)}', data_type='{data_type}', final='{output_name}'")
        
        logs_dir = str(ensure_devices_dir())
        
        self.status_label.config(text=f"Importing {len(files)} file(s)...", fg="blue")
        self.window.update()
        
        combined_records = []
        for file_key in files:
            content = self.importer.download_file(self.selected_bucket, file_key)
            if not content:
                continue
            
            if data_type == "dht":
                combined_records.extend(self.importer.parse_dht_data(content))
            elif data_type == "smartmeter":
                combined_records.extend(self.importer.parse_smartmeter_data(content))
        
        if not combined_records:
            self.status_label.config(text="✗ Import failed", fg="red")
            messagebox.showerror("Import Failed", "No data could be imported.")
            return
        
        if data_type == "dht":
            dest = os.path.join(logs_dir, f"{output_name}.csv")
            ok = self.importer.save_dht_to_csv(combined_records, dest)
        else:
            dest = os.path.join(logs_dir, f"{output_name}.csv")
            ok = self.importer.save_smartmeter_to_csv(combined_records, dest, device_name=output_name)
        
        if ok:
            self.status_label.config(text=f"✓ Imported to {dest}", fg="green")
            messagebox.showinfo(
                "Import Complete",
                f"Saved {len(combined_records)} rows to:\n{dest}\n\n"
                f"Use 'Generate graphs' in Simulation menu to visualize."
            )
        else:
            self.status_label.config(text="✗ Import failed", fg="red")
            messagebox.showerror("Import Failed", "Unable to save CSV.")

    def _import_all(self):
        if not self.selected_bucket:
            messagebox.showwarning("No Bucket", "Please select or enter a bucket first.")
            return

        data_type = self.data_type_var.get()
        
        # Determine default name and actual processing type
        if data_type == 'all':
            default_name = "combined_all"
            process_as = 'smartmeter'  # Default to smartmeter for 'all' mode
        else:
            default_name = "dht_all" if data_type == "dht" else "smartmeter_all"
            process_as = data_type
            
        output_name = tk.simpledialog.askstring(
            "Output CSV name",
            "Enter the name for the combined CSV (without .csv):",
            parent=self.window,
            initialvalue=default_name
        )
        if not output_name:
            return
        output_name = _sanitize_name(output_name)
        
        # Add prefix if not already present
        if process_as == "dht" and not output_name.startswith("dht_"):
            output_name = f"dht_{output_name}"
        elif process_as == "smartmeter" and not output_name.startswith("smartmeter_"):
            output_name = f"smartmeter_{output_name}"
        
        logs_dir = str(ensure_devices_dir())

        self.status_label.config(text=f"Importing ALL files from '{self.selected_bucket}'...", fg="blue")
        self.window.update()

        try:
            objects = self.importer.list_objects(self.selected_bucket)
        except Exception as e:
            self.status_label.config(text="✗ Import failed", fg="red")
            messagebox.showerror("Error", f"Cannot list objects:\n{e}")
            return

        # Filter by file type, then by content type
        target_keys = []
        for obj in objects:
            key = obj.get('Key', '')
            if not key.endswith(('.json', '.txt', '.log', '.csv')):
                continue
            
            content = self.importer.download_file(self.selected_bucket, key)
            if not content:
                continue
            
            detected_type = self.importer.detect_data_type(content)
            # Include file if data_type is 'all' or matches detected type
            if data_type == 'all' or detected_type == data_type:
                target_keys.append((key, detected_type or process_as))
        
        if not target_keys:
            self.status_label.config(text="No matching files found", fg="orange")
            msg = "No DHT22 files found" if data_type == "dht" else ("No Smart Meter files found" if data_type == "smartmeter" else "No JSON files found")
            messagebox.showinfo("No Files", f"{msg} in this bucket.")
            return

        combined_records = []
        for key, file_type in target_keys:
            content = self.importer.download_file(self.selected_bucket, key)
            if not content:
                continue
            # Use detected file type or fallback to process_as
            if file_type == "dht":
                combined_records.extend(self.importer.parse_dht_data(content))
            else:
                combined_records.extend(self.importer.parse_smartmeter_data(content))

        if not combined_records:
            self.status_label.config(text="✗ Import failed", fg="red")
            messagebox.showerror("Import Failed", "No data could be imported from the bucket.")
            return

        dest = os.path.join(logs_dir, f"{output_name}.csv")
        # Save based on process_as type
        if process_as == "dht":
            ok = self.importer.save_dht_to_csv(combined_records, dest)
        else:
            ok = self.importer.save_smartmeter_to_csv(combined_records, dest, device_name=output_name)

        if ok:
            self.status_label.config(text=f"✓ Imported {len(combined_records)} rows from {len(target_keys)} files", fg="green")
            messagebox.showinfo(
                "Import Complete",
                f"Saved {len(combined_records)} rows from {len(target_keys)} files to:\n{dest}\n\n"
                f"Use 'Generate graphs' to visualize."
            )
        else:
            self.status_label.config(text="✗ Import failed", fg="red")
            messagebox.showerror("Import Failed", "Unable to save CSV.")

    def _combine_all_json(self):
        if not self.selected_bucket:
            messagebox.showwarning("No Bucket", "Please select or enter a bucket first.")
            return

        data_type = self.data_type_var.get()
        default_name = "dht_all" if data_type == "dht" else "smartmeter_all"
        output_name = tk.simpledialog.askstring(
            "Output JSON name",
            "Enter the name for the combined JSON (without .json):",
            parent=self.window,
            initialvalue=default_name
        )
        if not output_name:
            return
        output_name = _sanitize_name(output_name)
        logs_dir = str(ensure_devices_dir())

        self.status_label.config(text=f"Combining ALL JSON from '{self.selected_bucket}'...", fg="blue")
        self.window.update()

        try:
            objects = self.importer.list_objects(self.selected_bucket)
        except Exception as e:
            self.status_label.config(text="✗ Combine failed", fg="red")
            messagebox.showerror("Error", f"Cannot list objects:\n{e}")
            return

        # Filter by file type, then by content type
        target_keys = []
        for obj in objects:
            key = obj.get('Key', '')
            if not key.endswith(('.json', '.txt', '.log', '.csv')):
                continue
            
            content = self.importer.download_file(self.selected_bucket, key)
            if not content:
                continue
            
            detected_type = self.importer.detect_data_type(content)
            if detected_type == data_type:
                target_keys.append((key, content))
        
        if not target_keys:
            self.status_label.config(text="No matching files found", fg="orange")
            msg = "No DHT22 files found" if data_type == "dht" else "No Smart Meter files found"
            messagebox.showinfo("No Files", f"{msg} in this bucket.")
            return

        combined_lines = []
        for key, content in target_keys:
            # keep as raw lines (newline-delimited JSON)
            combined_lines.extend([ln for ln in content.split('\n') if ln.strip()])

        if not combined_lines:
            self.status_label.config(text="✗ Combine failed", fg="red")
            messagebox.showerror("Combine Failed", "No data could be combined from the bucket.")
            return

        dest = os.path.join(logs_dir, f"{output_name}.json")
        try:
            with open(dest, 'w', encoding='utf-8') as f:
                f.write("\n".join(combined_lines))
            self.status_label.config(text=f"✓ Combined {len(combined_lines)} lines from {len(target_keys)} files", fg="green")
            messagebox.showinfo(
                "Combine Complete",
                f"Saved {len(combined_lines)} JSON lines from {len(target_keys)} files to:\n{dest}"
            )
        except Exception as e:
            self.status_label.config(text="✗ Combine failed", fg="red")
            messagebox.showerror("Combine Failed", f"Unable to save JSON file:\n{e}")
    
    def _preview_data(self):
        sel = self.files_listbox.curselection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a file to preview.")
            return
        
        file_key = self.files_listbox.get(sel[0])
        content = self.importer.download_file(self.selected_bucket, file_key)
        
        if not content:
            messagebox.showerror("Error", "Failed to download file.")
            return
        
        # Preview window
        prev = tk.Toplevel(self.window)
        prev.title(f"Preview: {file_key}")
        prev.geometry("700x500")
        
        tk.Label(prev, text=f"File: {file_key}", font=("Arial", 10, "bold")).pack(pady=5)
        
        text = scrolledtext.ScrolledText(prev, wrap=tk.WORD, width=80, height=25)
        text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Show first 100 lines
        lines = content.split('\n')[:100]
        text.insert(tk.END, '\n'.join(lines))
        if len(content.split('\n')) > 100:
            text.insert(tk.END, f"\n\n... (showing first 100 lines of {len(content.split(chr(10)))})")
        text.config(state=tk.DISABLED)
        
        tk.Button(prev, text="Close", command=prev.destroy).pack(pady=10)


def open_aws_telemetry_ui(parent: tk.Tk):
    """Open AWS telemetry import dialog."""
    AWSTelemetryImportUI(parent)
