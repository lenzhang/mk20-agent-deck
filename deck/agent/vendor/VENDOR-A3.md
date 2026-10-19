# A3 vendored third-party (unmodified)
- mbedtls/: Mbed TLS 3.6.7 LTS (2026-07-07), from https://github.com/Mbed-TLS/mbedtls/releases/download/mbedtls-3.6.7/mbedtls-3.6.7.tar.bz2
  sha256 a7e8bcbec0e6f761b4af24f25677626b35f762f68eef79c08677a363212d11f6 (matches release notes). Only include/, library/*.c|*.h, LICENSE kept. Apache-2.0.
- ca/cacert.pem: Mozilla CA bundle via curl.se, "Certificate data from Mozilla as of: Fri Sep 25 03:12:01 2026 GMT",
  sha256 a41b5d356aea97a529fe27e0f7316d2f9d946d75927476cf9cf1b90637d00505 (matches curl.se/ca/cacert.pem.sha256). MPL-2.0.
