"""
AWS S3 telemetry data importer for DHT22 and Smart Meter logs.
Imports time-series JSON data from S3 and applies it to bound sensors/devices.
"""
from __future__ import annotations

import json
import csv
from typing import Optional, List, Dict, Any
from datetime import datetime

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

logger = setup_logging("io.aws_telemetry")


def _first_present(data: Dict[str, Any], *keys: str):
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _csv_timestamp(value) -> str:
    """Keep ISO input and convert Unix seconds/milliseconds for CSV readers."""
    if value is None or value == "":
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric > 10000000000:
        numeric /= 1000
    try:
        return datetime.fromtimestamp(numeric).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    except (OSError, OverflowError, ValueError):
        return ""


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
            paginator = self.s3_client.get_paginator('list_objects_v2')
            objects = []
            for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
                objects.extend(page.get('Contents', []))
            return objects
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
                if not isinstance(data, dict):
                    continue
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
                if not isinstance(data, dict):
                    continue
                records.append({
                    'timestamp': _first_present(data, 'timestamp_iso', 'ts'),
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
                if not isinstance(data, dict):
                    continue
                records.append({
                    'timestamp': _first_present(data, 'ts', 'timestamp_iso'),
                    'power': _first_present(data, 'apower', 'power', 'power_w'),
                    'voltage': _first_present(data, 'voltage', 'voltage_v'),
                    'current': _first_present(data, 'current', 'current_a'),
                    'energy': _first_present(data, 'energy_total', 'energy')
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
                        _csv_timestamp(r.get('timestamp', '')),
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
                        _csv_timestamp(r.get('timestamp', '')),
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
