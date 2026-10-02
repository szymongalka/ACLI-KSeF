import base64
import hashlib
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from asef.db import Database
from asef.invoice import build_invoice, invoice_summary
from asef.ksef import KsefError
from asef.ksef_export import KsefExportClient, _read_archive
from asef.openclaw_bridge import MemoryTokenSecrets
from asef.service import AsefService

SAMPLE = json.loads((Path(__file__).parent.parent / 'examples/faktura.json').read_text())
NIP = SAMPLE['seller']['nip']
NUMBER = '1234567890-20260801-AAAAAAAAAAAA-01'
HWM = '2026-08-02T00:00:00+00:00'


def digest(data):
    return base64.b64encode(hashlib.sha256(data).digest()).decode()


def make_archive(xml, *, extra=None, wrong_hash=False):
    summary = invoice_summary(xml)
    metadata = {'ksefNumber': NUMBER, 'invoiceHash': digest(b'bad' if wrong_hash else xml),
                'formCode': {'systemCode': 'FA (3)'},
                'netAmount': summary['net_amount'], 'vatAmount': summary['vat_amount'],
                'grossAmount': summary['gross_amount']}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('_metadata.json', json.dumps({'invoices': [metadata]}))
        archive.writestr(NUMBER + '.xml', xml)
        if extra:
            archive.writestr(extra, b'not-an-invoice')
    return output.getvalue()


def client_factory(monkeypatch, *, tamper=False, foreign_url=False, truncate=False):
    blobs = {}
    original_client = httpx.Client

    def download(request):
        assert 'authorization' not in request.headers
        return httpx.Response(200, content=blobs[request.url.path])

    monkeypatch.setattr('asef.ksef_export.httpx.Client', lambda **kw:
                        original_client(**kw, transport=httpx.MockTransport(download)))
    monkeypatch.setattr('asef.ksef_export.time.sleep', lambda *_: None)

    class Fake(KsefExportClient):
        def __init__(self, environment, nip):
            super().__init__(environment, nip, http=object(), secrets=MemoryTokenSecrets('fictional'))

        def _encrypt_rsa(self, key, usage):
            assert usage == 'SymmetricKeyEncryption'
            self.key = key
            return 'encrypted-fictional-key', None

        def _json(self, method, path, **kwargs):
            if method == 'POST':
                assert path == '/invoices/exports' and kwargs['auth']
                body = kwargs['json']
                assert body['onlyMetadata'] is False
                assert body['filters']['dateRange']['restrictToPermanentStorageHwmDate']
                self.subject = body['filters']['subjectType']
                iv = base64.b64decode(body['encryption']['initializationVector'])
                self.package = {'invoiceCount': 0, 'size': 0, 'parts': [], 'isTruncated': truncate,
                                'compressionType': 'Zip', 'permanentStorageHwmDate': HWM}
                if self.subject in {'Subject1', 'Subject2'}:
                    payload = deepcopy(SAMPLE)
                    if self.subject == 'Subject2':
                        payload['seller'], payload['buyer'] = payload['buyer'], payload['seller']
                    archive = make_archive(build_invoice(payload))
                    self.package.update(invoiceCount=1, size=len(archive))
                    chunks = [archive[:len(archive)//2], archive[len(archive)//2:]]
                    for ordinal, chunk in enumerate(chunks, 1):
                        padder = padding.PKCS7(128).padder()
                        padded = padder.update(chunk) + padder.finalize()
                        cipher = Cipher(algorithms.AES(self.key), modes.CBC(iv)).encryptor()
                        encrypted = cipher.update(padded) + cipher.finalize()
                        path = f'/part{ordinal}'
                        blobs[path] = encrypted[:-1] + bytes([encrypted[-1] ^ 1]) if tamper else encrypted
                        self.package['parts'].append({'ordinalNumber': ordinal, 'method': 'GET',
                            'url': ('https://example.org' if foreign_url else 'https://storage.ksef.mf.gov.pl') + path,
                            'partSize': len(chunk), 'partHash': digest(chunk),
                            'encryptedPartSize': len(encrypted), 'encryptedPartHash': digest(encrypted)})
                return {'referenceNumber': 'fixture-export'}
            assert method == 'GET' and path == '/invoices/exports/fixture-export'
            return {'status': {'code': 200}, 'package': self.package}

    return Fake


def test_encrypted_multi_part_export_and_existing_sync_pipeline(tmp_path, monkeypatch):
    factory = client_factory(monkeypatch)
    # Both directions use distinct KSeF numbers in real packages. Separate profile
    # databases here let each direction independently prove the import contract.
    for subject, direction in [('Subject1', 'issued'), ('Subject2', 'received')]:
        class Selected(factory):
            def query_metadata(self, actual, start, page=0, end=None):
                return super().query_metadata(actual if actual == subject else 'Subject3', start, page, end)
        with Database(tmp_path / subject / 'db.sqlite3') as db:
            db.add_profile(NIP, 'Fixture', 'test')
            service = AsefService(db, client_factory=Selected)
            result = service.sync(NIP, '2026-08-01T00:00:00Z', 'test')
            assert result['imported'] == 1 and result['metadata_mismatches'] == 0
            row = db.connection.execute('select direction,xml,xml_sha256 from documents').fetchone()
            assert row['direction'] == direction and hashlib.sha256(row['xml']).hexdigest() == row['xml_sha256']
            assert db.connection.execute('select count(*) from sync_cursors').fetchone()[0] == 4
            assert service.sync(NIP, '2026-08-01T00:00:00Z', 'test')['imported'] == 0
            assert db.connection.execute('select count(*) from documents').fetchone()[0] == 1


@pytest.mark.parametrize('options', [{'tamper': True}, {'foreign_url': True}])
def test_failed_download_does_not_import_or_advance_cursor(tmp_path, monkeypatch, options):
    factory = client_factory(monkeypatch, **options)
    with Database(tmp_path / 'db.sqlite3') as db:
        db.add_profile(NIP, 'Fixture', 'test')
        with pytest.raises(KsefError):
            AsefService(db, client_factory=factory).sync(NIP, '2026-08-01T00:00:00Z', 'test')
        assert db.connection.execute('select count(*) from documents').fetchone()[0] == 0
        assert db.connection.execute('select count(*) from sync_cursors').fetchone()[0] == 0


@pytest.mark.parametrize('extra', ['../escape.xml', 'unexpected.xml'])
def test_zip_members_never_extracted_and_must_match_metadata(extra):
    with pytest.raises(KsefError):
        _read_archive(make_archive(build_invoice(SAMPLE), extra=extra), 1)


def test_xml_hash_and_package_count_are_verified():
    xml = build_invoice(SAMPLE)
    with pytest.raises(KsefError):
        _read_archive(make_archive(xml, wrong_hash=True), 1)
    with pytest.raises(KsefError):
        _read_archive(make_archive(xml), 2)


def test_truncated_package_returns_split_signal_without_downloading(monkeypatch):
    factory = client_factory(monkeypatch, truncate=True, foreign_url=True)
    with factory('test', NIP) as client:
        assert client.query_metadata('Subject1', '2026-08-01T00:00:00Z') == {'isTruncated': True}
        assert not client._export_xmls
