"""Entry point. Azure App Service (Linux, Python) finds ``app`` in app.py and
starts it with gunicorn automatically; locally run ``python app.py``."""
from khapi import create_app

app = create_app()

if __name__ == "__main__":
    # Development server only; never used on App Service.
    app.run(host="127.0.0.1", port=5000, debug=False)
