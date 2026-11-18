"""
AWS S3 import module for scenario data.
Requires: boto3, pandas (optional for data transformation)
Install with: pip install boto3 pandas
"""
from __future__ import annotations

import json
from typing import Optional, List, Dict, Any

try:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

from app.logging_setup import setup_logging

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
            with open(local_path, 'w', encoding='utf-8', newline='') as f:
                f.write(content)
            logger.info(f"Saved content to '{local_path}'")
            return True
        except Exception as e:
            logger.error(f"Error saving to '{local_path}': {e}")
            return False
