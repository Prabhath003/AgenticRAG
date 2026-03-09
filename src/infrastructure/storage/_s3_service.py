# -----------------------------------------------------------------------------
# Copyright (c) 2025 Backend
# All rights reserved.
#
# Developed by:
# Author: Prabhath Chellingi
# GitHub: https://github.com/Prabhath003
# Contact: prabhathchellingi2003@gmail.com
#
# This source code is licensed under the MIT License found in the LICENSE file
# in the root directory of this source tree.
# -----------------------------------------------------------------------------

import boto3
from botocore.exceptions import ClientError
from typing import List, Optional, Dict, Any, Type, Literal
from types import TracebackType
import threading
from ...config import Config
from ...log_creator import get_file_logger

logger = get_file_logger()

# Module-level singleton session to avoid creating new sessions repeatedly
_s3_session: Optional[boto3.Session] = None
_s3_session_lock = threading.Lock()  # Thread-safe singleton initialization


def _get_s3_session() -> boto3.Session:
    """Get or create the singleton S3 session (thread-safe lazy initialization)

    Uses double-checked locking pattern for efficient thread-safe access.
    """
    global _s3_session

    # First check without lock (fast path for subsequent calls)
    if _s3_session is not None:
        return _s3_session

    # Second check with lock (ensures only one thread initializes)
    with _s3_session_lock:
        if _s3_session is None:
            _s3_session = boto3.Session(
                aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY,
                region_name=Config.AWS_BEDROCK_REGION,
            )
            logger.debug("S3 session created (singleton - reused across all S3Service instances)")

    return _s3_session


class S3Service:
    """Service for managing file storage in AWS S3

    Uses a singleton session to reuse connections efficiently.
    Each instance creates a new client from the shared session.
    """

    def __init__(self):
        """Initialize S3 client using singleton session"""
        session = _get_s3_session()
        self.s3_client = session.client("s3")  # type: ignore
        self.bucket_name = Config.AWS_S3_BUCKET
        logger.debug(f"S3Service initialized with bucket: {self.bucket_name}")

    def __enter__(self) -> "S3Service":
        """Enter context manager - returns self"""
        logger.debug(f"Entering S3Service context manager")
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> Literal[False]:
        """Exit context manager - cleanup resources"""
        logger.debug(f"Exiting S3Service context manager")

        if exc_type is not None:
            logger.error(
                f"Exception occurred in S3Service context: {exc_type.__name__}: {exc_val}",
                exc_info=True,  # Let logger get exception info from sys.exc_info()
            )

        # Close S3 client connection
        try:
            self.s3_client.close()
            logger.debug("S3 client closed successfully")
        except Exception as e:
            logger.error(f"Error closing S3 client: {str(e)}")

        # Return False to propagate exceptions
        return False

    def upload_file(
        self,
        file_content: bytes,
        s3_key: str,
        content_type: str = "application/octet-stream",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """
        Upload file to S3

        Args:
            file_content: File bytes
            s3_key: S3 object key (path in bucket)
            content_type: MIME type
            metadata: Optional metadata dict

        Returns:
            S3 URL if successful, None otherwise
        """
        try:
            # Prepare metadata
            upload_metadata = metadata or {}

            # Upload to S3
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=file_content,
                ContentType=content_type,
                Metadata=upload_metadata,
            )

            s3_url = (
                f"https://{self.bucket_name}.s3.{Config.AWS_BEDROCK_REGION}.amazonaws.com/{s3_key}"
            )
            logger.info(f"File uploaded successfully to S3: {s3_key} ({len(file_content)} bytes)")

            return s3_url

        except ClientError as e:
            logger.error(f"S3 upload error for key {s3_key}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during S3 upload: {str(e)}")
            return None

    def download_file(self, s3_key: str) -> Optional[bytes]:
        """
        Download file from S3

        Args:
            s3_key: S3 object key

        Returns:
            File content as bytes, or None if failed
        """
        try:
            response = self.s3_client.get_object(Bucket=self.bucket_name, Key=s3_key)
            file_content = response["Body"].read()
            logger.info(
                f"File downloaded successfully from S3: {s3_key} ({len(file_content)} bytes)"
            )
            return file_content

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "NoSuchKey":
                logger.error(f"S3 file not found: {s3_key}")
            else:
                logger.error(f"S3 download error for key {s3_key}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during S3 download: {str(e)}")
            return None

    def delete_file(self, s3_key: str) -> bool:
        """
        Delete file from S3

        Args:
            s3_key: S3 object key

        Returns:
            True if successful, False otherwise
        """
        try:
            self.s3_client.delete_object(Bucket=self.bucket_name, Key=s3_key)
            logger.info(f"File deleted successfully from S3: {s3_key}")
            return True

        except ClientError as e:
            logger.error(f"S3 delete error for key {s3_key}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during S3 delete: {str(e)}")
            return False

    def file_exists(self, s3_key: str) -> bool:
        """
        Check if file exists in S3

        Args:
            s3_key: S3 object key

        Returns:
            True if exists, False otherwise
        """
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code == "404":
                return False
            logger.error(f"Error checking S3 file existence for {s3_key}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error checking S3 file: {str(e)}")
            return False

    def get_presigned_url(
        self,
        s3_key: str,
        expiration: int = 600,
        filename: Optional[str] = None,
        inline: bool = False,
    ) -> Optional[str]:
        """
        Generate presigned URL for temporary access

        Args:
            s3_key: S3 object key
            expiration: URL expiration time in seconds (default: 600 seconds)
            filename: Optional filename to use in Content-Disposition header
            inline: If True, render in browser (inline); if False, force download (attachment)

        Returns:
            Presigned URL or None if failed
        """
        try:
            params = {"Bucket": self.bucket_name, "Key": s3_key}

            # Add Content-Disposition header with filename if provided
            # inline=True renders in browser (e.g., PDFs, images), inline=False forces download
            if filename:
                disposition = "inline" if inline else "attachment"
                params["ResponseContentDisposition"] = f'{disposition}; filename="{filename}"'

            url = self.s3_client.generate_presigned_url(
                "get_object",
                Params=params,
                ExpiresIn=expiration,
            )
            logger.debug(
                f"Generated presigned URL for {s3_key}"
                + (f" with filename: {filename}" if filename else "")
            )
            return url

        except ClientError as e:
            logger.error(f"Presigned URL error for key {s3_key}: {str(e)}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error generating presigned URL: {str(e)}")
            return None

    def list_objects(self, prefix: str = "") -> list[Dict[str, Any]]:
        """
        List objects in S3 with optional prefix filter

        Args:
            prefix: S3 key prefix to filter objects

        Returns:
            List of object metadata dicts (Key, Size, LastModified, etc.)
        """
        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)

            objects: List[Dict[str, Any]] = []
            for page in pages:
                if "Contents" in page:
                    objects.extend([dict(obj) for obj in page["Contents"]])

            logger.debug(f"Listed {len(objects)} objects with prefix: {prefix}")
            return objects

        except ClientError as e:
            logger.error(f"S3 list error with prefix {prefix}: {str(e)}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error listing S3 objects: {str(e)}")
            return []

    def upload_file_from_path(self, local_path: str, s3_key: str) -> bool:
        """
        Upload file from local filesystem to S3

        Args:
            local_path: Path to local file
            s3_key: S3 object key (path in bucket)

        Returns:
            True if successful, False otherwise
        """
        try:
            self.s3_client.upload_file(local_path, self.bucket_name, s3_key)
            logger.info(f"File uploaded successfully to S3: {s3_key}")
            return True

        except ClientError as e:
            logger.error(f"S3 upload error for key {s3_key}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error uploading file: {str(e)}")
            return False

    def download_file_to_path(self, s3_key: str, local_path: str) -> bool:
        """
        Download file from S3 to local filesystem

        Args:
            s3_key: S3 object key
            local_path: Path to save downloaded file

        Returns:
            True if successful, False otherwise
        """
        try:
            self.s3_client.download_file(self.bucket_name, s3_key, local_path)
            logger.info(f"File downloaded successfully from S3: {s3_key} -> {local_path}")
            return True

        except ClientError as e:
            logger.error(f"S3 download error for key {s3_key}: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error downloading file: {str(e)}")
            return False


_s3_service_instance = None


def get_s3_service() -> S3Service:
    """Get singleton S3 service instance"""
    global _s3_service_instance
    if _s3_service_instance is None:
        _s3_service_instance = S3Service()
    return _s3_service_instance
