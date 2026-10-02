"""KSeF package retrieval using the existing sync/import interface.

Reference: MF API v2 /invoices/exports, AES-256-CBC + PKCS7, _metadata.json.
No credentials or signed download URLs are persisted or returned to the agent.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import time
import zipfile
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from .ksef import KsefClient, KsefError

MAX_ARCHIVE = 256 * 1024 * 1024
MAX_EXPANDED = 512 * 1024 * 1024


def _check_bytes(data: bytes, size: int, digest: str) -> None:
    if len(data) != size or base64.b64encode(hashlib.sha256(data).digest()).decode() != digest:
        raise KsefError("KSeF export size/hash mismatch")


def _decrypt_part(data: bytes, key: bytes, iv: bytes, part: dict) -> bytes:
    _check_bytes(data, part['encryptedPartSize'], part['encryptedPartHash'])
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    plain = unpadder.update(padded) + unpadder.finalize()
    _check_bytes(plain, part['partSize'], part['partHash'])
    return plain


def _read_archive(data: bytes, count: int) -> tuple[list[dict], dict[str, bytes]]:
    # Read members in memory; never extract paths from remote ZIP files.
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        if (len(names) > 10001 or len(set(names)) != len(names)
                or sum(item.file_size for item in infos) > MAX_EXPANDED
                or any('/' in name or '\\' in name or name in {'', '.', '..'} for name in names)):
            raise KsefError("Invalid KSeF export archive")
        metadata = json.loads(archive.read('_metadata.json'))
        invoices = metadata['invoices']
        if not isinstance(invoices, list) or len(invoices) != count:
            raise KsefError("KSeF export invoice count mismatch")
        xmls = {}
        for item in invoices:
            number = item['ksefNumber']
            if not re.fullmatch(r'[0-9]{10}-[0-9]{8}-[A-Za-z0-9-]{1,40}', number) or number in xmls:
                raise KsefError("Invalid KSeF export invoice identifier")
            xml = archive.read(number + '.xml')
            if base64.b64encode(hashlib.sha256(xml).digest()).decode() != item['invoiceHash']:
                raise KsefError("KSeF invoice XML hash mismatch")
            xmls[number] = xml
        if set(names) != {'_metadata.json', *(number + '.xml' for number in xmls)}:
            raise KsefError("Unexpected KSeF export members")
        return invoices, xmls


class KsefExportClient(KsefClient):
    """Return export metadata and verified XML to AsefService.sync unchanged."""

    def query_metadata(self, subject_type: str, date_from: str, page_offset: int = 0,
                       date_to: str | None = None) -> dict:
        if page_offset != 0:
            raise KsefError("Package export does not use metadata pagination")
        self._export_xmls = {}
        # An export is one read-only server job; do not retry ambiguous POSTs.
        key, iv = os.urandom(32), os.urandom(16)
        encrypted, key_id = self._encrypt_rsa(key, 'SymmetricKeyEncryption')
        encryption = {'encryptedSymmetricKey': encrypted,
                      'initializationVector': base64.b64encode(iv).decode()}
        if key_id:
            encryption['publicKeyId'] = key_id
        dates = {'dateType': 'PermanentStorage', 'from': date_from,
                 'restrictToPermanentStorageHwmDate': True}
        if date_to:
            dates['to'] = date_to
        result = self._json('POST', '/invoices/exports', auth=True, json={
            'encryption': encryption, 'onlyMetadata': False, 'compressionType': 'Zip',
            'filters': {'subjectType': subject_type, 'formType': 'FA', 'dateRange': dates}})
        reference = result['referenceNumber']
        if not isinstance(reference, str) or not re.fullmatch(r'[A-Za-z0-9-]{1,100}', reference):
            raise KsefError("Invalid KSeF export reference")
        for _ in range(180):
            time.sleep(2)
            result = self._json('GET', '/invoices/exports/' + reference, auth=True)
            code = result['status']['code']
            if code == 200:
                break
            if code != 100:
                raise KsefError("KSeF package export failed", code)
        else:
            raise KsefError("KSeF package export not ready before timeout")
        package = result['package']
        if package.get('isTruncated') is True:
            # Existing service bisects the range before any cursor is advanced.
            return {'isTruncated': True}
        if (package.get('isTruncated') is not False or package.get('compressionType') != 'Zip'
                or not package.get('permanentStorageHwmDate')):
            raise KsefError("KSeF export missing completeness evidence")
        count = package['invoiceCount']
        if type(count) is not int or not 0 <= count <= 10000:
            raise KsefError("Invalid KSeF export invoice count")
        parts = sorted(package['parts'], key=lambda part: part['ordinalNumber'])
        if [p['ordinalNumber'] for p in parts] != list(range(1, len(parts) + 1)):
            raise KsefError("Invalid KSeF export part sequence")
        if sum(p['encryptedPartSize'] for p in parts) > MAX_ARCHIVE:
            raise KsefError("KSeF export exceeds local memory bound")
        if not parts and count:
            raise KsefError("KSeF export parts missing")
        plain_parts = []
        # Separate unauthenticated client: bearer tokens never reach storage URLs.
        with httpx.Client(timeout=120, follow_redirects=False) as download:
            for part in parts:
                url = urlsplit(part['url'])
                if (part['method'] != 'GET' or url.scheme != 'https' or url.username or url.password
                        or url.port not in (None, 443) or not url.hostname
                        or not (url.hostname == 'mf.gov.pl' or url.hostname.endswith('.mf.gov.pl'))):
                    raise KsefError("KSeF export storage URL outside official MF host scope")
                content = bytearray()
                with download.stream('GET', part['url']) as response:
                    if response.status_code != 200:
                        raise KsefError("KSeF export download failed", response.status_code)
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > min(part['encryptedPartSize'], MAX_ARCHIVE):
                            raise KsefError("KSeF export download too large")
                plain_parts.append(_decrypt_part(bytes(content), key, iv, part))
        data = b''.join(plain_parts)
        if len(data) != package['size']:
            raise KsefError("KSeF export package size mismatch")
        invoices, self._export_xmls = _read_archive(data, count) if parts else ([], {})
        return {'invoices': invoices, 'hasMore': False, 'isTruncated': False,
                'permanentStorageHwmDate': package['permanentStorageHwmDate']}

    def get_invoice(self, ksef_number: str) -> bytes:
        try:
            return self._export_xmls[ksef_number]
        except KeyError:
            raise KsefError("Invoice missing from verified KSeF package") from None

    def close(self) -> None:
        self._export_xmls = {}
        super().close()
