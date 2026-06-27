import os, sys

if getattr(sys, 'frozen', False):
    base = sys._MEIPASS
    cert = os.path.join(base, 'certifi', 'cacert.pem')
    if os.path.exists(cert):
        os.environ['REQUESTS_CA_BUNDLE'] = cert
        os.environ['SSL_CERT_FILE']      = cert
