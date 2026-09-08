from django.apps import AppConfig
import threading
import sys

class BlocksConfig(AppConfig):
    name = 'blocks'

    def ready(self):
        # Don't run warmup during management commands (like migrate, test, etc.)
        if 'runserver' in sys.argv or 'gunicorn' in sys.argv or 'uwsgi' in sys.argv:
            def _warmup():
                try:
                    from .views import _get_all_blocks, _get_all_assessments, _get_all_quarries, _get_all_officers, _get_all_audit_logs
                    _get_all_blocks()
                    _get_all_assessments()
                    _get_all_quarries()
                    _get_all_officers()
                    _get_all_audit_logs()
                    print("[WARMUP] MongoDB Atlas cache pre-warmed successfully!")
                except Exception as e:
                    print(f"[WARMUP] Cache warmup info: {e}")
            t = threading.Thread(target=_warmup, daemon=True)
            t.start()
