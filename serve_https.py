"""Run the Django portal directly over HTTPS without IIS or admin privileges."""
import argparse
import os
import ssl
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--certificate', required=True, type=Path, help='PEM server certificate, including intermediate chain if needed')
    parser.add_argument('--private-key', required=True, type=Path, help='PEM private key; keep outside source control')
    parser.add_argument('--address', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8443)
    parser.add_argument('--database', type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535')
    for file in (args.certificate, args.private_key):
        if not file.is_file():
            parser.error('Certificate and private key files must exist')
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'portal.settings_local')
    os.environ['DJANGO_DEBUG'] = 'false'
    os.environ['DJANGO_SECURE_COOKIES'] = 'true'
    if args.database:
        os.environ['DJANGO_DB_PATH'] = str(args.database.resolve())
    from django.conf import settings
    from portal.wsgi import application
    from whitenoise import WhiteNoise
    from cheroot.wsgi import Server
    from cheroot.ssl.builtin import BuiltinSSLAdapter
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(args.certificate), str(args.private_key))
    adapter = BuiltinSSLAdapter(str(args.certificate), str(args.private_key))
    adapter.context = context
    app = WhiteNoise(application, root=str(settings.STATIC_ROOT), prefix=settings.STATIC_URL)
    server = Server((args.address, args.port), app, numthreads=10)
    server.ssl_adapter = adapter
    print(f'HTTPS portal listening on {args.address}:{args.port}; Ctrl+C to stop', flush=True)
    try:
        server.start()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()


if __name__ == '__main__':
    main()
