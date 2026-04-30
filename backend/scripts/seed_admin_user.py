"""
One-shot: promote a single user to admin_role='admin'.

Usage:
    PYTHONPATH=. python -m backend.scripts.seed_admin_user --email sharonyoutube1@gmail.com

The user must already exist in app_user (they signed in once via OAuth).
Idempotent: safe to re-run.
"""
from __future__ import annotations
import argparse
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except ImportError:
    pass

from backend.app.db.phase2_session import Phase2SessionLocal
from backend.app.models.orm.user_orm import AppUser


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--email", required=True)
    args = p.parse_args()

    db = Phase2SessionLocal()
    try:
        user = db.query(AppUser).filter(AppUser.email == args.email).first()
        if user is None:
            print(f"[ERROR] no app_user with email {args.email}", file=sys.stderr)
            print("Sign in with this email at least once before running this script.",
                  file=sys.stderr)
            return 2
        if user.admin_role == "admin":
            print(f"[ok] {args.email} is already admin")
            return 0
        user.admin_role = "admin"
        db.commit()
        print(f"[ok] promoted {args.email} -> admin_role='admin'")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
