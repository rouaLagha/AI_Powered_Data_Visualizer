from __future__ import annotations

try:
    from backend.sql_model_api.main import main
except ImportError:
    from sql_model_api.main import main


if __name__ == "__main__":
    main()
