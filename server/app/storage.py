"""Object Storage. OVHcloud Object Storage is S3-compatible; MinIO stands in locally; Azure Blob Storage (STORAGE_BACKEND=azure)."""

import asyncio
import base64
import hashlib
import uuid
from functools import lru_cache
from urllib.parse import quote, urlsplit

from app import config


class MemoryStorage:
    """For tests. Download and upload links point at the server itself (/api/_memory-storage/…)."""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.multipart: dict[str, dict] = {}   # upload id → {"key", "content_type", "parts": {n: bytes}}

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    async def put_stream(self, key: str, fileobj, content_type: str) -> None:
        self.objects[key] = (fileobj.read(), content_type)

    async def get(self, key: str) -> tuple[bytes, str]:
        return self.objects[key]

    async def download_to(self, key: str, path: str) -> None:
        with open(path, "wb") as f:
            f.write(self.objects[key][0])

    async def head(self, key: str) -> int | None:
        obj = self.objects.get(key)
        return len(obj[0]) if obj else None

    async def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    async def download_url(self, key: str, filename: str) -> str:
        return f"/api/_memory-storage/{key}"

    # Direct upload (the browser PUTs the bytes). The memory links are only served while this backend is in use.
    async def presign_put(self, key: str, content_type: str) -> str:
        return f"/api/_memory-storage/{key}?content_type={quote(content_type)}"

    async def start_multipart(self, key: str, content_type: str) -> str:
        upload_id = uuid.uuid4().hex
        self.multipart[upload_id] = {"key": key, "content_type": content_type, "parts": {}}
        return upload_id

    async def presign_part(self, key: str, upload_id: str, number: int) -> str:
        return f"/api/_memory-storage/{key}?upload_id={upload_id}&part={number}"

    def put_part(self, key: str, upload_id: str, number: int, data: bytes) -> str:
        up = self.multipart.get(upload_id)
        if up is None or up["key"] != key:
            raise KeyError(upload_id)
        up["parts"][number] = data
        return '"' + hashlib.md5(data).hexdigest() + '"'

    async def complete_multipart(self, key: str, upload_id: str, parts: list[dict], content_type: str | None = None
                                 ) -> None:
        up = self.multipart.pop(upload_id)
        data = b""
        for p in sorted(parts, key=lambda p: p["PartNumber"]):
            chunk = up["parts"][p["PartNumber"]]
            if '"' + hashlib.md5(chunk).hexdigest() + '"' != p["ETag"]:
                raise ValueError("ETag passer ikke")
            data += chunk
        self.objects[key] = (data, up["content_type"])

    async def abort_multipart(self, key: str, upload_id: str) -> None:
        self.multipart.pop(upload_id, None)

    def ensure_cors(self, origin: str) -> bool:
        return False


class S3Storage:
    def __init__(self):
        import boto3
        from botocore.config import Config

        s = config.settings
        self.bucket = s.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.s3_endpoint,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )
        # Upload links are signed for the address the browser uses (S3_PUBLIC_ENDPOINT, else S3_ENDPOINT).
        self.public = self.client if not s.s3_public_endpoint else boto3.client(
            "s3",
            endpoint_url=s.s3_public_endpoint,
            region_name=s.s3_region,
            aws_access_key_id=s.s3_access_key,
            aws_secret_access_key=s.s3_secret_key,
            config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        )

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        await asyncio.to_thread(self.client.put_object, Bucket=self.bucket, Key=key, Body=data,
                                ContentType=content_type)

    async def get(self, key: str) -> tuple[bytes, str]:
        obj = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(obj["Body"].read), obj.get("ContentType", "application/octet-stream")

    async def put_stream(self, key: str, fileobj, content_type: str) -> None:
        """Streams a file object to the bucket (multipart for big ones), without holding it in memory."""
        await asyncio.to_thread(self.client.upload_fileobj, fileobj, self.bucket, key,
                                ExtraArgs={"ContentType": content_type})

    async def download_to(self, key: str, path: str) -> None:
        await asyncio.to_thread(self.client.download_file, self.bucket, key, path)

    async def head(self, key: str) -> int | None:
        from botocore.exceptions import ClientError
        try:
            obj = await asyncio.to_thread(self.client.head_object, Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return int(obj["ContentLength"])

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    # Direct upload: the browser PUTs to these links; the bucket needs CORS for ORIGIN (ensure_cors, job storage.cors).
    async def presign_put(self, key: str, content_type: str) -> str:
        return await asyncio.to_thread(
            self.public.generate_presigned_url, "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=config.settings.upload_link_seconds,
        )

    async def start_multipart(self, key: str, content_type: str) -> str:
        r = await asyncio.to_thread(self.client.create_multipart_upload, Bucket=self.bucket, Key=key,
                                    ContentType=content_type)
        return r["UploadId"]

    async def presign_part(self, key: str, upload_id: str, number: int) -> str:
        return await asyncio.to_thread(
            self.public.generate_presigned_url, "upload_part",
            Params={"Bucket": self.bucket, "Key": key, "UploadId": upload_id, "PartNumber": number},
            ExpiresIn=config.settings.upload_link_seconds,
        )

    async def complete_multipart(self, key: str, upload_id: str, parts: list[dict], content_type: str | None = None
                                 ) -> None:
        await asyncio.to_thread(self.client.complete_multipart_upload, Bucket=self.bucket, Key=key,
                                UploadId=upload_id, MultipartUpload={"Parts": parts})

    async def abort_multipart(self, key: str, upload_id: str) -> None:
        await asyncio.to_thread(self.client.abort_multipart_upload, Bucket=self.bucket, Key=key, UploadId=upload_id)

    def ensure_cors(self, origin: str) -> bool:
        """Lets the app's origin PUT to upload links and read the ETag (multipart). Keeps other rules on the bucket.
        Returns whether anything changed."""
        from botocore.exceptions import ClientError
        try:
            rules = self.client.get_bucket_cors(Bucket=self.bucket).get("CORSRules", [])
        except ClientError:
            rules = []
        for r in rules:
            if origin in r.get("AllowedOrigins", []) and "PUT" in r.get("AllowedMethods", []) \
                    and "ETag" in r.get("ExposeHeaders", []):
                return False
        rules = [r for r in rules if origin not in r.get("AllowedOrigins", [])] + [{
            "AllowedOrigins": [origin], "AllowedMethods": ["PUT", "GET", "HEAD"], "AllowedHeaders": ["*"],
            "ExposeHeaders": ["ETag"], "MaxAgeSeconds": 3600}]
        self.client.put_bucket_cors(Bucket=self.bucket, CORSConfiguration={"CORSRules": rules})
        return True

    async def download_url(self, key: str, filename: str) -> str:
        safe = filename.replace('"', "")
        return await asyncio.to_thread(
            self.client.generate_presigned_url, "get_object",
            Params={"Bucket": self.bucket, "Key": key,
                    "ResponseContentDisposition": f'attachment; filename="{safe}"'},
            ExpiresIn=config.settings.download_link_seconds,
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)


class AzureBlobStorage:
    """Azure Blob Storage (STORAGE_BACKEND=azure). Same interface as S3Storage: links are SAS tokens signed with the
    account key, and multipart is Put Block + Put Block List (blocks that are never committed expire after 7 days)."""

    # A single Put Blob from the browser must say which kind of blob it creates; Put Block answers without an ETag.
    put_headers = {"x-ms-blob-type": "BlockBlob"}
    part_etags = False

    def __init__(self):
        import logging

        from azure.storage.blob import BlobServiceClient

        logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
        s = config.settings
        if s.azure_storage_connection_string:
            self.service = BlobServiceClient.from_connection_string(s.azure_storage_connection_string)
        else:
            self.service = BlobServiceClient(f"https://{s.azure_storage_account}.blob.core.windows.net",
                                             credential={"account_name": s.azure_storage_account,
                                                         "account_key": s.azure_storage_key})
        self.account = self.service.account_name
        self.key = self.service.credential.account_key
        self.container_name = s.azure_storage_container or s.s3_bucket
        self.container = self.service.get_container_client(self.container_name)

    def _blob(self, key: str):
        return self.container.get_blob_client(key)

    def _sas(self, key: str, *, write: bool, seconds: int, **extra) -> str:
        from datetime import datetime, timedelta, timezone

        from azure.storage.blob import BlobSasPermissions, generate_blob_sas
        perm = BlobSasPermissions(write=True, create=True) if write else BlobSasPermissions(read=True)
        token = generate_blob_sas(self.account, self.container_name, key, account_key=self.key, permission=perm,
                                  start=datetime.now(timezone.utc) - timedelta(minutes=5),
                                  expiry=datetime.now(timezone.utc) + timedelta(seconds=seconds), **extra)
        return f"{self._blob(key).url}?{token}"

    @staticmethod
    def _block_id(upload_id: str, number: int) -> str:
        """Same length for every block of a blob; the SDK base64-encodes it, the part link must do it itself."""
        return f"{upload_id}-{number:05d}"

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        from azure.storage.blob import ContentSettings
        await asyncio.to_thread(self._blob(key).upload_blob, data, overwrite=True,
                                content_settings=ContentSettings(content_type=content_type))

    async def get(self, key: str) -> tuple[bytes, str]:
        def read():
            d = self._blob(key).download_blob()
            return d.readall(), d.properties.content_settings.content_type or "application/octet-stream"
        return await asyncio.to_thread(read)

    async def put_stream(self, key: str, fileobj, content_type: str) -> None:
        from azure.storage.blob import ContentSettings
        await asyncio.to_thread(self._blob(key).upload_blob, fileobj, overwrite=True,
                                content_settings=ContentSettings(content_type=content_type))

    async def download_to(self, key: str, path: str) -> None:
        def save():
            with open(path, "wb") as f:
                self._blob(key).download_blob().readinto(f)
        await asyncio.to_thread(save)

    async def head(self, key: str) -> int | None:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            props = await asyncio.to_thread(self._blob(key).get_blob_properties)
        except ResourceNotFoundError:
            return None
        return int(props.size)

    async def delete(self, key: str) -> None:
        from azure.core.exceptions import ResourceNotFoundError
        try:
            await asyncio.to_thread(self._blob(key).delete_blob)
        except ResourceNotFoundError:
            pass

    async def presign_put(self, key: str, content_type: str) -> str:
        return self._sas(key, write=True, seconds=config.settings.upload_link_seconds)

    async def start_multipart(self, key: str, content_type: str) -> str:
        return uuid.uuid4().hex

    async def presign_part(self, key: str, upload_id: str, number: int) -> str:
        url = self._sas(key, write=True, seconds=config.settings.upload_link_seconds)
        return f"{url}&comp=block&blockid={quote(base64.b64encode(self._block_id(upload_id, number).encode()).decode(), safe='')}"

    async def complete_multipart(self, key: str, upload_id: str, parts: list[dict], content_type: str | None = None
                                 ) -> None:
        from azure.storage.blob import BlobBlock, ContentSettings
        blocks = [BlobBlock(block_id=self._block_id(upload_id, p["PartNumber"]))
                  for p in sorted(parts, key=lambda p: p["PartNumber"])]
        await asyncio.to_thread(self._blob(key).commit_block_list, blocks,
                                content_settings=ContentSettings(content_type=content_type) if content_type else None)

    async def abort_multipart(self, key: str, upload_id: str) -> None:
        return None   # uncommitted blocks are removed by Azure after 7 days

    def ensure_cors(self, origin: str) -> bool:
        """CORS is per storage account (shared by several instances): adds a rule for this origin, keeps the rest."""
        from azure.storage.blob import CorsRule
        rules = self.service.get_service_properties().get("cors") or []
        for r in rules:
            if origin in r.allowed_origins.split(",") and "PUT" in r.allowed_methods.split(","):
                return False
        rules = [r for r in rules if origin not in r.allowed_origins.split(",")] + [
            CorsRule([origin], ["PUT", "GET", "HEAD", "OPTIONS"], allowed_headers=["*"], exposed_headers=["*"],
                     max_age_in_seconds=3600)]
        self.service.set_service_properties(cors=rules)
        return True

    async def download_url(self, key: str, filename: str) -> str:
        safe = filename.replace('"', "")
        return self._sas(key, write=False, seconds=config.settings.download_link_seconds,
                         content_disposition=f'attachment; filename="{safe}"')

    def ensure_bucket(self) -> None:
        from azure.core.exceptions import ResourceExistsError
        try:
            self.container.create_container()
        except ResourceExistsError:
            pass


def public_origin() -> str | None:
    """The origin the browser uploads to (for the CSP's connect-src), or None when it is the app itself."""
    s = config.settings
    if s.storage_backend == "memory" or not s.direct_upload:
        return None
    if s.storage_backend == "azure":
        account = s.azure_storage_account
        if not account and s.azure_storage_connection_string:
            parts = dict(p.split("=", 1) for p in s.azure_storage_connection_string.split(";") if "=" in p)
            account = parts.get("AccountName")
        return f"https://{account}.blob.core.windows.net" if account else None
    url = urlsplit(s.s3_public_endpoint or s.s3_endpoint or "")
    return f"{url.scheme}://{url.netloc}" if url.scheme in ("http", "https") and url.netloc else None


@lru_cache
def get_storage():
    if config.settings.storage_backend == "memory":
        return MemoryStorage()
    if config.settings.storage_backend == "azure":
        return AzureBlobStorage()
    return S3Storage()
