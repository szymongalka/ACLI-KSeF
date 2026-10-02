"""Build the signed KSeF AuthTokenRequest from a registered certificate."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric import ec, rsa
from lxml import etree
from signxml import SignatureMethod, methods
from signxml.xades import XAdESSigner


AUTH_NS = "http://ksef.mf.gov.pl/auth/token/2.1"
SUBJECT_TYPES = {"certificateSubject", "certificateFingerprint"}


def signature_algorithm(private_key: object) -> SignatureMethod:
    """Select a KSeF-supported XAdES algorithm and enforce its minimum key size."""
    if isinstance(private_key, ec.EllipticCurvePrivateKey):
        if private_key.key_size < 256:
            raise ValueError("Klucz EC certyfikatu musi mieć co najmniej 256 bitów.")
        return SignatureMethod.ECDSA_SHA256
    if isinstance(private_key, rsa.RSAPrivateKey):
        if private_key.key_size < 2048:
            raise ValueError("Klucz RSA certyfikatu musi mieć co najmniej 2048 bitów.")
        return SignatureMethod.RSA_SHA256
    raise ValueError("Certyfikat KSeF wymaga klucza EC albo RSA.")


def signed_auth_request(challenge: str, nip: str, subject_type: str,
                        certificate_pem: bytes, private_key: object) -> bytes:
    if subject_type not in SUBJECT_TYPES:
        raise ValueError("Typ identyfikatora: certificateSubject albo certificateFingerprint.")
    algorithm = signature_algorithm(private_key)
    root = etree.Element(f"{{{AUTH_NS}}}AuthTokenRequest", nsmap={None: AUTH_NS})
    etree.SubElement(root, f"{{{AUTH_NS}}}Challenge").text = challenge
    context = etree.SubElement(root, f"{{{AUTH_NS}}}ContextIdentifier")
    etree.SubElement(context, f"{{{AUTH_NS}}}Nip").text = nip
    etree.SubElement(root, f"{{{AUTH_NS}}}SubjectIdentifierType").text = subject_type
    signed = XAdESSigner(
        method=methods.enveloped,
        signature_algorithm=algorithm,
        digest_algorithm="sha256",
    ).sign(root, key=private_key, cert=certificate_pem, always_add_key_value=False)
    return etree.tostring(signed, encoding="UTF-8", xml_declaration=True)
