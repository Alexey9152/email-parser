"""Entry point for the local UI: python ui.py

The map/report UI is at ``/`` and the per-recipient mailer at ``/send``.
"""

from email_parser.webui import main

if __name__ == "__main__":
    raise SystemExit(main())
