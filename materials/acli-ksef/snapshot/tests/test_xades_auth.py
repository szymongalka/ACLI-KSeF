import base64
from datetime import datetime, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml.xades import XAdESVerifier

from asef.xades_auth import AUTH_NS, signature_algorithm, signed_auth_request


@pytest.mark.parametrize("algorithm", ["ec", "rsa"])
def test_ksef_xades_auth_request_signs_and_verifies_with_fictional_certificate(algorithm: str) -> None:
    key = ec.generate_private_key(ec.SECP256R1()) if algorithm == "ec" else rsa.generate_private_key(
        public_exponent=65537, key_size=2048
    )
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fictional KSeF signer")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject).issuer_name(subject).public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2025, 1, 1, tzinfo=timezone.utc))
        .not_valid_after(datetime(2030, 1, 1, tzinfo=timezone.utc))
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=False, key_cert_sign=False,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    xml = signed_auth_request("fictional-challenge", "1111111111", "certificateSubject", cert_pem, key)
    root = etree.fromstring(xml)
    namespace = {"auth": AUTH_NS, "ds": "http://www.w3.org/2000/09/xmldsig#"}
    assert root.tag == f"{{{AUTH_NS}}}AuthTokenRequest"
    assert root.xpath("string(auth:Challenge)", namespaces=namespace) == "fictional-challenge"
    assert root.xpath("string(auth:ContextIdentifier/auth:Nip)", namespaces=namespace) == "1111111111"
    assert root.xpath("string(auth:SubjectIdentifierType)", namespaces=namespace) == "certificateSubject"
    assert len(root.xpath(".//ds:SignedInfo/ds:Reference", namespaces=namespace)) == 3
    assert len(XAdESVerifier().verify(root, x509_cert=cert_pem, expect_references=3)) == 3
    if algorithm == "ec":
        signature = base64.b64decode(root.xpath("string(.//ds:SignatureValue)", namespaces=namespace))
        assert len(signature) == 64  # XMLDSIG uses fixed-width R || S for P-256.


@pytest.mark.parametrize("algorithm,minimum", [("rsa", 2048), ("ec", 256)])
def test_ksef_xades_rejects_keys_below_minimum_size(algorithm: str, minimum: int) -> None:
    weak_key = (rsa.generate_private_key(public_exponent=65537, key_size=1024)
                if algorithm == "rsa" else ec.generate_private_key(ec.SECP224R1()))
    with pytest.raises(ValueError, match=f"{minimum} bitów"):
        signature_algorithm(weak_key)
    with pytest.raises(ValueError, match=f"{minimum} bitów"):
        signed_auth_request("fictional-challenge", "1111111111", "certificateSubject", b"unused", weak_key)
