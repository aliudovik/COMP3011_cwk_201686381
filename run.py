import argparse
from app import create_app
from app.extensions import db

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-db", action="store_true")
    args = parser.parse_args()

    app = create_app()

    if args.init_db:
        with app.app_context():
            db.create_all()

    app.run(host="0.0.0.0", port=7777, debug=app.config.get("DEBUG", False))

if __name__ == "__main__":
    main()
