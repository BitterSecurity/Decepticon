"""Exercise security-sensitive dependency paths without credentials or network."""

import sys

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from langgraph_cli.exec import subp_exec
from pyasn1.codec.der import decoder, encoder
from pyasn1_modules import rfc5208


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stdin", ["fixture-token-not-a-secret", "first-fixture-line\nsecond-fixture-line"]
)
async def test_verbose_cli_does_not_echo_stdin(stdin, capfd):
    """Keep command/output diagnostics while withholding arbitrary stdin."""
    stdout, stderr = await subp_exec(
        sys.executable,
        "-c",
        "import sys; assert sys.stdin.read(); print('child-received-input')",
        input=stdin,
        verbose=True,
        collect=True,
    )
    captured = capfd.readouterr()
    assert stdout == "child-received-input\n"
    assert stderr is None
    assert sys.executable in captured.out
    assert "child-received-input" in captured.out
    for line in stdin.splitlines():
        assert line not in captured.out + captured.err


def test_asn1_decodes_and_round_trips_pkcs8():
    """Exercise ASN.1 parsing with an ephemeral key, never a real identity."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    parsed, remainder = decoder.decode(der, asn1Spec=rfc5208.PrivateKeyInfo())
    assert remainder == b""
    assert str(parsed["privateKeyAlgorithm"]["algorithm"]) == "1.2.840.113549.1.1.1"
    assert encoder.encode(parsed) == der
    restored = serialization.load_der_private_key(encoder.encode(parsed), password=None)
    payload = b"dependency-compatibility-fixture"
    signature = restored.sign(payload, padding.PKCS1v15(), hashes.SHA256())
    key.public_key().verify(signature, payload, padding.PKCS1v15(), hashes.SHA256())
